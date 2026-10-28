"""Historical-only Freqtrade replay; never starts a trading bot."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from threading import Thread
import webbrowser

ENGINE_VERSION = '2026.6'
DEFAULT_TIMERANGE = '20210101-20260808'
PAIRS = ('BTC', 'ETH')
WARMUP_CANDLES = 220
SUBPROCESS_TIMEOUT = 240
ROOT = Path(__file__).resolve().parents[1]
SAFE_ENV = frozenset({
    'PATH', 'HOME', 'TMPDIR', 'TEMP', 'TMP', 'LANG', 'TZ', 'SYSTEMROOT',
    'SSL_CERT_FILE', 'SSL_CERT_DIR', 'REQUESTS_CA_BUNDLE',
    'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY',
    'http_proxy', 'https_proxy', 'all_proxy', 'no_proxy',
})


class PracticeError(RuntimeError):
    """Missing or unsafe prerequisites: do not produce a pretend report."""


def safe_environment(source: dict[str, str]) -> dict[str, str]:
    """Allowlist avoids keys, tokens, .env loading, PYTHONPATH and FT overrides."""
    return {key: value for key, value in source.items()
            if key in SAFE_ENV or key.startswith('LC_')}


def validate_config(config: dict) -> None:
    expected = {'dry_run': True, 'dry_run_wallet': 1000, 'stake_amount': 50,
                'trading_mode': 'spot', 'strategy': 'S1TrendFollow',
                'stake_currency': 'USDT'}
    if any(config.get(key) != value for key, value in expected.items()):
        raise PracticeError('练习配置已变化：仅允许固定虚拟本金的现货历史回放。')
    exchange = config.get('exchange', {})
    if any(exchange.get(key) for key in ('key', 'secret', 'password', 'uid')):
        raise PracticeError('练习配置不接受交易所凭据。')
    if (exchange.get('pair_whitelist') != ['BTC/USDT', 'ETH/USDT']
            or not 0 < config.get('max_open_trades', 99) <= 3
            or not 0 < config.get('tradable_balance_ratio', 1) <= .99
            or config.get('stoploss_on_exchange') is not True
            or config.get('order_types', {}).get('stoploss_on_exchange') is not True):
        raise PracticeError('练习配置违反固定交易对或风控约束。')
    if any(config.get(key, {}).get('enabled') is not False
           for key in ('telegram', 'api_server', 'webhook')):
        raise PracticeError('练习配置必须关闭通知、控制接口与 webhook。')


def load_practice_config(root: Path) -> dict:
    config = json.loads((root / 'deploy/user_data/config/practice.json').read_text())
    validate_config(config)
    return config


def engine_python(root: Path) -> Path:
    executable = root / '.venv-engine/bin/python'
    if not executable.is_file():
        raise PracticeError('缺少历史回放运行环境 .venv-engine，请先完成一次性安装。')
    return executable


def check_engine(python: Path, env: dict, *, execute=subprocess.run) -> None:
    result = execute([str(python), '-m', 'freqtrade', '--version'], env=env,
                     capture_output=True, text=True, timeout=30, check=False)
    if result.returncode or not re.search(r'freqtrade\s+2026\.6(?:\s|$)',
                                         result.stdout, re.IGNORECASE):
        raise PracticeError(f'历史回放只支持 Freqtrade {ENGINE_VERSION}，当前环境不符。')


def parse_timerange(value: str) -> tuple[datetime, datetime]:
    try:
        start, end = (datetime.strptime(part, '%Y%m%d').replace(tzinfo=timezone.utc)
                      for part in value.split('-'))
    except (ValueError, TypeError):
        raise PracticeError('日期范围必须为 YYYYMMDD-YYYYMMDD。') from None
    bear_start = datetime(2022, 1, 1, tzinfo=timezone.utc)
    bear_end = datetime(2023, 1, 1, tzinfo=timezone.utc)
    if start >= end or start > bear_start or end < bear_end:
        raise PracticeError('练习区间必须完整包含 2022 年熊市。')
    return start, end


def _validate_candle_file(path: Path, start: datetime, end: datetime,
                          step: timedelta) -> None:
    if not path.is_file():
        raise PracticeError(f'缺少本地行情数据：{path.name}')
    try:
        rows = json.loads(path.read_text())
        timestamps = [row[0] for row in rows]
        valid_values = all(len(row) == 6 and all(math.isfinite(v) for v in row)
                           and min(row[1:5]) > 0 and row[5] >= 0 for row in rows)
        valid_order = all(b > a for a, b in zip(timestamps, timestamps[1:]))
    except (ValueError, TypeError, IndexError, KeyError):
        raise PracticeError(f'行情数据格式错误：{path.name}') from None
    expected = set(range(int(start.timestamp()*1000), int(end.timestamp()*1000),
                         int(step.total_seconds()*1000)))
    if not valid_values or not valid_order or not expected.issubset(timestamps):
        raise PracticeError(f'行情数据存在缺口、重复或无效价格：{path.name}')


def validate_data(data_dir: Path, timerange: str, timeframe: str) -> None:
    start, end = parse_timerange(timerange)
    step = timedelta(days=1) if timeframe == '1d' else timedelta(hours=4)
    for pair in PAIRS:
        _validate_candle_file(data_dir / f'{pair}_USDT-{timeframe}.json',
                              start-step*WARMUP_CANDLES, end, step)


def detail_available(data_dir: Path, timerange: str, timeframe: str) -> bool:
    if timeframe != '1d':
        return False
    start, end = parse_timerange(timerange)
    try:
        for pair in PAIRS:
            _validate_candle_file(data_dir / f'{pair}_USDT-4h.json', start, end,
                                  timedelta(hours=4))
    except PracticeError:
        return False
    return True


def backtest_command(python: Path, root: Path, run_dir: Path, data_dir: Path,
                     timerange: str, timeframe: str, detail: bool) -> list[str]:
    command = [str(python), '-m', 'freqtrade', 'backtesting',
               '--config', str(run_dir / 'practice.json'),
               '--userdir', str(run_dir / 'user_data'),
               '--strategy-path', str(root / 'deploy/user_data/strategies'),
               '--strategy', 'S1TrendFollow', '--datadir', str(data_dir),
               '--data-format-ohlcv', 'json', '--timerange', timerange,
               '--timeframe', timeframe, '--fee', '0.001',
               '--enable-protections', '--cache', 'none', '--export', 'trades',
               '--export-directory', str(run_dir)]
    if detail:
        command.extend(['--timeframe-detail', '4h'])
    return command


def result_archive(run_dir: Path) -> Path:
    candidates = list(run_dir.glob('backtest-result-*.zip'))
    if len(candidates) != 1 or candidates[0].stat().st_size == 0:
        raise PracticeError('Freqtrade 未产生唯一有效回测结果，停止生成报告。')
    return candidates[0]


def default_data_dir(root: Path) -> Path:
    own = root / 'deploy/user_data/data/binance'
    if (own / 'BTC_USDT-4h.json').is_file():
        return own
    sibling = root.parent / 'finance/deploy/user_data/data/binance'
    return sibling if sibling != own else own


@contextmanager
def metadata_source(snapshot: Path | None):
    """Optional real public exchangeInfo snapshot, never an execution emulator."""
    if snapshot is None:
        yield None
        return
    payload = snapshot.read_bytes()
    info = json.loads(payload)
    if {s.get('symbol') for s in info.get('symbols', [])} != {'BTCUSDT', 'ETHUSDT'}:
        raise PracticeError('公开交易规格快照必须仅包含 BTCUSDT 与 ETHUSDT。')
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.split('?')[0] != '/api/v3/exchangeInfo':
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(payload)
        def log_message(self, *_args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}/api/v3'
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def write_run_inputs(root: Path, run_dir: Path, args, metadata_url: str | None) -> None:
    config = load_practice_config(root)
    config['timeframe'] = args.timeframe
    if metadata_url:
        for key in ('ccxt_config', 'ccxt_async_config'):
            config['exchange'][key]['urls']['api']['public'] = metadata_url
        (run_dir / 'exchangeInfo.json').write_bytes(args.market_snapshot.read_bytes())
    (run_dir / 'user_data').mkdir()
    (run_dir / 'practice.json').write_text(json.dumps(config, indent=2))
    manifest = {'mode': 'historical_backtest', 'engine': ENGINE_VERSION,
                'timerange': args.timerange, 'timeframe': args.timeframe,
                'virtual_wallet': 1000, 'stake_amount': 50, 'fee_per_side': .001,
                'current_news_veto': False,
                'metadata_limits_date': '2026-10-28' if metadata_url else 'current_api',
                'market_metadata': 'public_snapshot' if metadata_url else 'public_api',
                'metadata_sha256': hashlib.sha256(args.market_snapshot.read_bytes()).hexdigest()
                    if metadata_url else None,
                'strategy_files_sha256': {
                    name: hashlib.sha256((root / 'deploy/user_data/strategies' / name).read_bytes()).hexdigest()
                    for name in ('S1TrendFollow.py', 'base.py')},
                'data_files_sha256': {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                    for path in args.data_dir.glob('*_USDT-*.json')
                    if path.name.startswith(('BTC_', 'ETH_'))}}
    (run_dir / 'manifest.json').write_text(json.dumps(manifest, indent=2))


def publish_latest(output: Path) -> Path:
    """Publish complete report artifacts atomically, keeping every replay folder."""
    summary = output.with_suffix('.json')
    if not output.is_file() or not summary.is_file():
        raise PracticeError('报告或核对摘要未完整生成，不更新最新报告。')
    payloads = {'.json': summary.read_bytes(), '.html': output.read_bytes()}
    json.loads(payloads['.json'])
    target_dir = output.parent.parent
    for suffix, payload in payloads.items():
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=target_dir, prefix='.latest-', delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target_dir / ('latest' + suffix))
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    return target_dir / 'latest.html'


def execute_replay(args, root: Path = ROOT) -> Path:
    env = safe_environment(dict(os.environ))
    python = engine_python(root)
    check_engine(python, env)
    validate_data(args.data_dir, args.timerange, args.timeframe)
    load_practice_config(root)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    run_dir = Path(tempfile.mkdtemp(prefix='replay-', dir=args.output_dir))
    detail = detail_available(args.data_dir, args.timerange, args.timeframe)
    with metadata_source(args.market_snapshot) as metadata_url:
        if metadata_url:
            env['NO_PROXY'] = env['no_proxy'] = '127.0.0.1,localhost'
        write_run_inputs(root, run_dir, args, metadata_url)
        command = backtest_command(python, root, run_dir, args.data_dir,
                                   args.timerange, args.timeframe, detail)
        (run_dir / 'command.json').write_text(json.dumps(command, indent=2))
        with (run_dir / 'backtest.log').open('w') as logfile:
            result = subprocess.run(command, cwd=root, env=env, stdout=logfile,
                                    stderr=subprocess.STDOUT, timeout=SUBPROCESS_TIMEOUT)
        if result.returncode:
            raise PracticeError(f'历史回放未完成，详情见 {run_dir / "backtest.log"}')
    archive = result_archive(run_dir)
    output = run_dir / 'report.html'
    report = [str(python), '-m', 'practice.report', '--result', str(archive),
              '--data-dir', str(args.data_dir), '--output', str(output),
              '--strategy', 'S1TrendFollow']
    subprocess.run(report, cwd=root, env=env, check=True, timeout=SUBPROCESS_TIMEOUT)
    if not output.is_file():
        raise PracticeError('报告没有生成，停止打开浏览器。')
    publish_latest(output)
    return output


def argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Sentinel：仅历史回放，虚拟本金 1,000 USDT')
    parser.add_argument('--data-dir', type=Path, default=default_data_dir(ROOT))
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'practice/output')
    parser.add_argument('--timerange', default=DEFAULT_TIMERANGE)
    parser.add_argument('--timeframe', choices=('1d', '4h'), default='4h')
    parser.add_argument('--market-snapshot', type=Path,
                        default=ROOT / 'practice/binance-spot-markets.json',
                        help='已获取的公开 Binance exchangeInfo JSON，用于离线重复回放')
    parser.add_argument('--open', action='store_true', help='完成后打开本地中文报告')
    return parser


def main() -> int:
    args = argument_parser().parse_args()
    args.data_dir = args.data_dir.expanduser().resolve()
    args.output_dir = args.output_dir.expanduser().resolve()
    print('正在进行历史行情回放；使用虚拟 1,000 USDT，不连接交易账户。', flush=True)
    try:
        output = execute_replay(args)
    except (PracticeError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f'无法完成历史回放：{exc}', file=sys.stderr)
        return 1
    print(f'回放完成：{output}', flush=True)
    if args.open:
        latest = args.output_dir / 'latest.html'
        webbrowser.open((latest if latest.is_file() else output).as_uri())
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
