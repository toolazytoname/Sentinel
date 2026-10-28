"""Safety contracts for the historical-only practice launcher."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock

import pytest

from practice import run

ROOT = Path(__file__).resolve().parents[2]


def test_environment_drops_all_inherited_credentials_and_framework_overrides():
    source = {
        'PATH': '/usr/bin', 'HOME': '/tmp/user', 'HTTPS_PROXY': 'http://localhost:7890',
        'FREQTRADE__DRY_RUN': 'false', 'FREQTRADE__EXCHANGE__KEY': 'secret',
        'EXCHANGE_SECRET': 'secret', 'BINANCE_API_KEY': 'secret',
        'TELEGRAM_TOKEN': 'secret', 'OPENAI_API_KEY': 'secret', 'PYTHONPATH': '/evil',
    }
    clean = run.safe_environment(source)
    assert clean == {'PATH': '/usr/bin', 'HOME': '/tmp/user',
                     'HTTPS_PROXY': 'http://localhost:7890'}
    assert source['FREQTRADE__DRY_RUN'] == 'false'


def test_learning_config_has_virtual_wallet_and_no_control_or_credentials():
    config = run.load_practice_config(ROOT)
    assert config['dry_run'] is True
    assert config['trading_mode'] == 'spot'
    assert config['dry_run_wallet'] == 1000
    assert config['stake_amount'] == 50
    assert config['timeframe'] == '4h'
    assert config['max_open_trades'] <= 3
    assert config['tradable_balance_ratio'] <= .99
    assert config['exchange']['pair_whitelist'] == ['BTC/USDT', 'ETH/USDT']
    assert all(config['exchange'].get(key, '') == '' for key in ('key','secret','password'))
    assert config['order_types']['stoploss_on_exchange'] is True
    assert not config['telegram']['enabled']
    assert not config['api_server']['enabled']
    assert not config['webhook']['enabled']


@pytest.mark.parametrize('field,value', [('dry_run', False), ('stake_amount', 1000),
                                         ('dry_run_wallet', 100000), ('trading_mode', 'futures')])
def test_config_changes_fail_closed(field, value):
    config = run.load_practice_config(ROOT)
    config[field] = value
    with pytest.raises(run.PracticeError):
        run.validate_config(config)


def test_command_only_backtests_and_exports_real_engine_results(tmp_path):
    cmd = run.backtest_command(Path('/engine/python'), ROOT, tmp_path,
                               tmp_path / 'data', '20210101-20260808', '1d', True)
    assert cmd[:4] == ['/engine/python', '-m', 'freqtrade', 'backtesting']
    assert '--enable-protections' in cmd
    assert cmd[cmd.index('--fee')+1] == '0.001'
    assert cmd[cmd.index('--cache')+1] == 'none'
    assert cmd[cmd.index('--export')+1] == 'trades'
    assert cmd[cmd.index('--timeframe-detail')+1] == '4h'
    assert '--trade' not in cmd and 'trade' not in cmd and '--live' not in cmd
    assert str(tmp_path / 'practice.json') in cmd


def _candles(path, start, end, step):
    value = start
    rows = []
    while value < end:
        rows.append([int(value.timestamp()*1000), 100, 102, 99, 101, 5])
        value += step
    path.write_text(json.dumps(rows))


@pytest.fixture
def candle_dir(tmp_path):
    start = datetime(2020, 1, 1, tzinfo=timezone.utc)
    end = datetime(2023, 1, 2, tzinfo=timezone.utc)
    for symbol in ('BTC', 'ETH'):
        _candles(tmp_path / f'{symbol}_USDT-1d.json', start, end, timedelta(days=1))
        _candles(tmp_path / f'{symbol}_USDT-4h.json', start, end, timedelta(hours=4))
    return tmp_path


def test_data_requires_both_pairs_warmup_and_complete_window(candle_dir):
    run.validate_data(candle_dir, '20210101-20230102', '4h')
    path = candle_dir / 'ETH_USDT-4h.json'
    rows = json.loads(path.read_text())
    path.write_text(json.dumps(rows[:4000] + rows[4001:]))
    with pytest.raises(run.PracticeError, match='缺口'):
        run.validate_data(candle_dir, '20210101-20230102', '4h')


def test_missing_runtime_and_result_fail_closed(tmp_path):
    with pytest.raises(run.PracticeError, match='运行环境'):
        run.engine_python(tmp_path)
    with pytest.raises(run.PracticeError, match='结果'):
        run.result_archive(tmp_path)


def test_checks_engine_version_before_launch():
    execute = Mock(return_value=Mock(returncode=0, stdout='Freqtrade 2026.5\n', stderr=''))
    with pytest.raises(run.PracticeError, match='2026.6'):
        run.check_engine(Path('/engine/python'), {}, execute=execute)


def test_no_live_cli_and_default_bear_market_window():
    parser = run.argument_parser()
    args = parser.parse_args([])
    assert args.timerange == '20210101-20260808'
    assert args.timeframe == '4h'
    with pytest.raises(SystemExit):
        parser.parse_args(['--live'])


def test_timerange_requires_full_bear_market():
    with pytest.raises(run.PracticeError, match='2022'):
        run.parse_timerange('20240101-20250101')


def test_report_only_runs_after_result_exists(tmp_path):
    (tmp_path / 'backtest-result-a.zip').write_bytes(b'archive')
    assert run.result_archive(tmp_path).name == 'backtest-result-a.zip'
    (tmp_path / 'backtest-result-b.zip').write_bytes(b'other')
    with pytest.raises(run.PracticeError):
        run.result_archive(tmp_path)


def _arguments(tmp_path, candle_dir):
    return run.argument_parser().parse_args([
        '--data-dir', str(candle_dir), '--output-dir', str(tmp_path / 'output'),
        '--timerange', '20210101-20230102',
    ])


def test_replay_isolates_outputs_and_passes_only_clean_environment(monkeypatch, tmp_path, candle_dir):
    args = _arguments(tmp_path, candle_dir)
    monkeypatch.setenv('FREQTRADE__DRY_RUN', 'false')
    monkeypatch.setenv('EXCHANGE_SECRET', 'must-not-leak')
    monkeypatch.setattr(run, 'engine_python', lambda root: Path('/engine/python'))
    monkeypatch.setattr(run, 'check_engine', lambda *args: None)
    calls = []
    def execute(command, **kwargs):
        calls.append(command)
        assert 'FREQTRADE__DRY_RUN' not in kwargs['env']
        assert 'EXCHANGE_SECRET' not in kwargs['env']
        if 'backtesting' in command:
            target = Path(command[command.index('--export-directory')+1])
            assert not (target / 'report.html').exists()
            assert target.parent == args.output_dir
            (target / 'backtest-result-real-shape.zip').write_bytes(b'archive')
        else:
            output = Path(command[command.index('--output')+1])
            output.write_text('<html>报告</html>')
            output.with_suffix('.json').write_text('{"final": 1000}')
        return Mock(returncode=0)
    monkeypatch.setattr(run.subprocess, 'run', execute)
    first = run.execute_replay(args, ROOT)
    second = run.execute_replay(args, ROOT)
    assert first != second and first.is_file() and second.is_file()
    assert len(calls) == 4
    assert json.loads((first.parent / 'practice.json').read_text())['dry_run'] is True
    assert json.loads((first.parent / 'manifest.json').read_text())['mode'] == 'historical_backtest'
    assert (args.output_dir / 'latest.html').read_text() == second.read_text()
    manifest = json.loads((first.parent / 'manifest.json').read_text())
    assert set(manifest['strategy_files_sha256']) == {'S1TrendFollow.py', 'base.py'}


@pytest.mark.parametrize('failure', ['error', 'no-result', 'timeout'])
def test_engine_failure_never_reaches_report(monkeypatch, tmp_path, candle_dir, failure):
    args = _arguments(tmp_path, candle_dir)
    monkeypatch.setattr(run, 'engine_python', lambda root: Path('/engine/python'))
    monkeypatch.setattr(run, 'check_engine', lambda *args: None)
    def execute(command, **kwargs):
        assert 'backtesting' in command
        if failure == 'timeout':
            raise run.subprocess.TimeoutExpired(command, 1)
        return Mock(returncode=1 if failure == 'error' else 0)
    monkeypatch.setattr(run.subprocess, 'run', execute)
    with pytest.raises((run.PracticeError, run.subprocess.TimeoutExpired)):
        run.execute_replay(args, ROOT)
    assert not list(args.output_dir.rglob('report.html'))


def test_public_snapshot_server_only_serves_exchange_metadata(tmp_path):
    from urllib.request import urlopen
    from urllib.error import HTTPError
    path = tmp_path / 'exchangeInfo.json'
    payload = {'symbols': [{'symbol':'BTCUSDT'}, {'symbol':'ETHUSDT'}]}
    path.write_text(json.dumps(payload))
    with run.metadata_source(path) as url:
        with urlopen(url + '/exchangeInfo') as response:
            assert json.load(response) == payload
        with pytest.raises(HTTPError):
            urlopen(url + '/order')
    path.write_text('{"symbols": []}')
    with pytest.raises(run.PracticeError):
        with run.metadata_source(path):
            pass


def test_snapshot_is_copied_only_to_new_run(tmp_path, candle_dir):
    args = _arguments(tmp_path, candle_dir)
    args.market_snapshot = tmp_path / 'market.json'
    args.market_snapshot.write_text('{"symbols": []}')
    run_dir = tmp_path / 'fresh'
    run_dir.mkdir()
    run.write_run_inputs(ROOT, run_dir, args, 'http://127.0.0.1:1234/api/v3')
    config = json.loads((run_dir / 'practice.json').read_text())
    assert config['exchange']['ccxt_config']['urls']['api']['public'].startswith('http://127.0.0.1:')
    assert (run_dir / 'exchangeInfo.json').read_text() == args.market_snapshot.read_text()


def test_daily_detail_is_optional_but_never_fabricated(candle_dir):
    assert run.detail_available(candle_dir, '20210101-20230102', '1d')
    assert not run.detail_available(candle_dir, '20210101-20230102', '4h')
    (candle_dir / 'BTC_USDT-4h.json').unlink()
    assert not run.detail_available(candle_dir, '20210101-20230102', '1d')
    run.validate_data(candle_dir, '20210101-20230102', '1d')


def test_bad_data_and_bad_dates_fail_closed(tmp_path):
    with pytest.raises(run.PracticeError):
        run.parse_timerange('tomorrow')
    with pytest.raises(run.PracticeError):
        run.validate_data(tmp_path, '20210101-20230102', '4h')
    (tmp_path / 'BTC_USDT-4h.json').write_text('[null]')
    with pytest.raises(run.PracticeError):
        run.validate_data(tmp_path, '20210101-20230102', '4h')


def test_main_handles_missing_runtime_without_opening_browser(monkeypatch, capsys):
    monkeypatch.setattr(run.sys, 'argv', ['practice.run', '--open'])
    monkeypatch.setattr(run, 'execute_replay', Mock(side_effect=run.PracticeError('缺少运行环境')))
    browser = Mock()
    monkeypatch.setattr(run.webbrowser, 'open', browser)
    assert run.main() == 1
    assert '缺少运行环境' in capsys.readouterr().err
    browser.assert_not_called()


def test_main_opens_only_completed_report(monkeypatch, tmp_path):
    report = tmp_path / 'report.html'
    report.write_text('<html>报告</html>')
    monkeypatch.setattr(run.sys, 'argv', ['practice.run', '--open', '--output-dir', str(tmp_path)])
    monkeypatch.setattr(run, 'execute_replay', Mock(return_value=report))
    browser = Mock()
    monkeypatch.setattr(run.webbrowser, 'open', browser)
    assert run.main() == 0
    browser.assert_called_once_with(report.as_uri())


def test_main_opens_latest_report_in_explicit_output_directory(monkeypatch, tmp_path):
    replay = tmp_path / 'replay-one'
    replay.mkdir()
    report = replay / 'report.html'
    report.write_text('complete run')
    latest = tmp_path / 'latest.html'
    latest.write_text('complete latest')
    monkeypatch.setattr(run.sys, 'argv', ['practice.run', '--open', '--output-dir', str(tmp_path)])
    monkeypatch.setattr(run, 'execute_replay', Mock(return_value=report))
    browser = Mock()
    monkeypatch.setattr(run.webbrowser, 'open', browser)
    assert run.main() == 0
    browser.assert_called_once_with(latest.as_uri())


def test_bundled_metadata_provenance_matches_snapshot():
    import hashlib
    provenance = json.loads((ROOT / 'practice/binance-spot-markets.provenance.json').read_text())
    snapshot = ROOT / 'practice/binance-spot-markets.json'
    assert hashlib.sha256(snapshot.read_bytes()).hexdigest() == provenance['sha256']
    assert provenance['source_url'].startswith('https://data-api.binance.vision/')
    assert {row['symbol'] for row in json.loads(snapshot.read_text())['symbols']} == {'BTCUSDT','ETHUSDT'}


def test_latest_keeps_previous_report_until_all_new_artifacts_exist(tmp_path):
    replay = tmp_path / 'replay-one'
    replay.mkdir()
    output = replay / 'report.html'
    latest = tmp_path / 'latest.html'
    latest.write_text('previous-success')
    output.write_text('incomplete-new-report')
    with pytest.raises(run.PracticeError):
        run.publish_latest(output)
    assert latest.read_text() == 'previous-success'
    output.with_suffix('.json').write_text('{"final": 1000}')
    assert run.publish_latest(output) == latest
    assert latest.read_text() == 'incomplete-new-report'
    assert output.read_text() == 'incomplete-new-report'
    assert json.loads((tmp_path / 'latest.json').read_text()) == {'final': 1000}
    assert not list(tmp_path.glob('.latest-*'))


def test_malformed_summary_cannot_replace_latest(tmp_path):
    replay = tmp_path / 'replay-one'
    replay.mkdir()
    (tmp_path / 'latest.html').write_text('last-good')
    output = replay / 'report.html'
    output.write_text('new')
    output.with_suffix('.json').write_text('invalid')
    with pytest.raises(ValueError):
        run.publish_latest(output)
    assert (tmp_path / 'latest.html').read_text() == 'last-good'


def test_native_freqtrade_loads_and_validates_exact_replay_config(tmp_path):
    """Real framework schema + strategy validation, with no exchange networking."""
    python = ROOT / '.venv-engine/bin/python'
    if not python.is_file():
        pytest.skip('One-time native engine environment not installed')
    args = _arguments(tmp_path, tmp_path)
    run_dir = tmp_path / 'native-config'
    run_dir.mkdir()
    run.write_run_inputs(ROOT, run_dir, args, None)
    command = run.backtest_command(python, ROOT, run_dir, tmp_path,
                                   args.timerange, args.timeframe, False)
    script = '''
import json, sys
from freqtrade.commands.arguments import Arguments
from freqtrade.configuration import Configuration
from freqtrade.configuration.config_validation import validate_config_consistency
from freqtrade.enums import RunMode
from freqtrade.resolvers import StrategyResolver
arguments = Arguments(json.loads(sys.argv[1])).get_parsed_arg()
config = Configuration(arguments, RunMode.BACKTEST).get_config()
validate_config_consistency(config, preliminary=True)
strategy = StrategyResolver.load_strategy(config)
validate_config_consistency(config)
assert config['dry_run'] is True
assert config['api_server']['enabled'] is False
assert config['api_server']['listen_ip_address'] == '127.0.0.1'
assert config['trading_mode'] == 'spot'
assert strategy.use_custom_stoploss is True
assert {item['method'] for item in strategy.protections} == {'MaxDrawdown','StoplossGuard','CooldownPeriod'}
print('NATIVE_REPLAY_CONFIG_VALID')
'''
    result = run.subprocess.run([str(python), '-c', script, json.dumps(command[3:])],
                                cwd=ROOT, env=run.safe_environment(dict(run.os.environ)),
                                capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'NATIVE_REPLAY_CONFIG_VALID' in result.stdout
