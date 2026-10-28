"""Economic fixtures for the read-only learning report (no trade simulation)."""
import json
from zipfile import ZipFile

import pytest

from practice import report
from practice.economics import build_summary, validate_bars

DAY = 86_400_000
START = 1_704_067_200_000


def bars(prices):
    return [[START + i * DAY, p, p, p, p, 1] for i, p in enumerate(prices)]


def result(trades=None):
    return {
        'strategy_name': 'S1TrendFollow', 'trading_mode': 'spot', 'timeframe': '1d',
        'starting_balance': 1000, 'final_balance': 1000, 'fee': .001,
        'backtest_start_ts': START, 'backtest_end_ts': START + 2 * DAY,
        'stake_currency': 'USDT', 'stake_amount': 50, 'trades': trades or [],
    }


def trade(**changes):
    value = dict(pair='BTC/USDT', amount=1, open_rate=100, close_rate=110,
                 fee_open=.001, fee_close=.001, open_timestamp=START,
                 close_timestamp=START + 2 * DAY, profit_abs=9.79,
                 is_open=False, is_short=False, leverage=1,
                 enter_tag='s1_trend_entry', exit_reason='exit_signal',
                 orders=[{'amount': 1, 'safe_price': 100, 'ft_is_entry': True},
                         {'amount': 1, 'safe_price': 110, 'ft_is_entry': False}])
    value.update(changes)
    return value


def prices():
    return {'BTC/USDT': bars([100, 120, 110]), 'ETH/USDT': bars([100, 80, 100])}


def test_round_trip_fees_equity_drawdown_and_equal_weight_benchmark():
    value = result([trade()]); value['final_balance'] = 1009.79
    equity = [(START, 1000), (START + DAY, 1020), (START + 2 * DAY, 1010)]
    summary = build_summary(value, prices(), equity)
    assert summary['fees_total'] == pytest.approx(.21)
    assert summary['profit'] == pytest.approx(9.79)
    assert summary['drawdown'] == pytest.approx((1019.9 - 1009.79) / 1019.9)
    assert summary['benchmark_final'] == pytest.approx(1000 / 1.001 * 1.05 * .999)
    assert summary['trades'][0]['entry_fee'] == pytest.approx(.1)
    assert summary['trades'][0]['exit_fee'] == pytest.approx(.11)
    assert summary['benchmark'][1][1] == pytest.approx(1000 / 1.001)


def test_zero_trades_flat_cash_but_benchmark_has_entry_fee():
    summary = build_summary(result(), prices(), [])
    assert summary['trade_count'] == 0
    assert summary['equity'] == [(START, 1000), (START + DAY, 1000), (START + 2 * DAY, 1000)]
    assert summary['fees_total'] == summary['drawdown'] == 0
    assert summary['benchmark'][0][1] < 1000


def test_forced_close_is_not_a_new_sell_signal_and_final_wallet_is_adjusted():
    value = result([trade(exit_reason='force_exit', is_open=True)])
    value['final_balance'] = 1009.79
    equity = [(START, 1000), (START + DAY, 1020), (START + 2 * DAY, 1010)]
    summary = build_summary(value, prices(), equity)
    assert summary['forced_close_count'] == 1
    assert summary['equity'][-1][1] == pytest.approx(1009.79)
    assert summary['equity'][-2][1] == pytest.approx(1009.9)
    assert '结束' in summary['trades'][0]['exit_explanation']
    assert summary['trades'][0]['held_until_end']
    assert summary['terminal_holdings_value'] == pytest.approx(110)
    assert summary['terminal_unrealized'] == pytest.approx(9.9)
    assert '市值合计 110.00' in report.render_html(summary)


def test_real_unrealized_drawdown_is_not_closed_profit_drawdown():
    value = result([trade()]); value['final_balance'] = 1009.79
    equity = [(START, 1000), (START + DAY, 850.1), (START + 2 * DAY, 1010)]
    assert build_summary(value, prices(), equity)['drawdown'] == pytest.approx(.15)


@pytest.mark.parametrize('mutate', [
    lambda b: b.reverse(), lambda b: b.append(b[-1]),
    lambda b: b.pop(1), lambda b: b[1].__setitem__(4, float('nan')),
    lambda b: b[1].__setitem__(1, 0),
])
def test_invalid_price_data_rejected(mutate):
    data = bars([100, 120, 110]); mutate(data)
    with pytest.raises(ValueError):
        validate_bars(data, START, START + 2 * DAY, DAY)


@pytest.mark.parametrize('changes', [dict(is_short=True), dict(leverage=2),
                                      dict(profit_abs=900), dict(close_rate=-1)])
def test_unsupported_or_inconsistent_trade_rejected(changes):
    value = result([trade(**changes)]); value['final_balance'] = 1009.79
    with pytest.raises(ValueError):
        build_summary(value, prices(), [(START, 1000), (START + DAY, 1020), (START + 2 * DAY, 1010)])


def test_missing_wallet_and_mismatched_terminal_balance_rejected():
    value = result([trade()]); value['final_balance'] = 1009.79
    with pytest.raises(ValueError, match='钱包'):
        build_summary(value, prices(), [])
    with pytest.raises(ValueError, match='核对'):
        build_summary(value, prices(), [(START, 1000), (START + DAY, 1000), (START + 2 * DAY, 1100)])


def test_offline_html_escapes_untrusted_tags():
    value = result([trade(enter_tag='<script>alert(1)</script>')])
    value['final_balance'] = 1009.79
    summary = build_summary(value, prices(), [(START, 1000), (START + DAY, 1020), (START + 2 * DAY, 1010)])
    html = report.render_html(summary)
    assert '<script>alert(1)</script>' not in html
    assert '&lt;script&gt;' in html
    assert '<svg' in html and '未实现' in html and '全部' in html
    assert 'https://' not in html and 'http://' not in html


def test_zip_json_input_and_ohlcv_loading(tmp_path):
    archive = tmp_path / 'engine.zip'
    with ZipFile(archive, 'w') as z:
        z.writestr('engine.json', json.dumps({'strategy': {'S1TrendFollow': result()}}))
        z.writestr('engine_config.json', json.dumps({'fee': .001, 'trading_mode': 'spot'}))
        z.writestr('ignored.pkl', b'never read or execute')
    for pair, data in prices().items():
        (tmp_path / f"{pair.replace('/', '_')}-1d.json").write_text(json.dumps(data))
    summary = report.generate_report(archive, tmp_path, tmp_path / 'result.html')
    assert summary['trade_count'] == 0
    assert (tmp_path / 'result.json').exists()
    assert (tmp_path / 'result.html').exists()


@pytest.mark.parametrize('field,value', [
    ('trading_mode', 'futures'), ('timeframe', '1h'), ('stake_currency', 'BTC'),
    ('fee', -.1), ('starting_balance', 0), ('final_balance', float('inf')),
    ('backtest_end_ts', START), ('backtest_start_ts', START + 1),
])
def test_invalid_result_scope_and_numbers_rejected(field, value):
    item = result(); item[field] = value
    with pytest.raises(ValueError):
        build_summary(item, prices(), [])


@pytest.mark.parametrize('changes', [dict(fee_open=-.001), dict(fee_close=1),
    dict(open_timestamp=START - DAY), dict(close_timestamp=START - DAY),
    dict(is_open=True), dict(amount=True), dict(amount=float('nan')),
    dict(orders=[{'ft_is_entry': True}])])
def test_trade_boundaries_and_execution_scope(changes):
    value = result([trade(**changes)]); value['final_balance'] = 1009.79
    with pytest.raises(ValueError):
        build_summary(value, prices(), [])


def test_missing_pair_and_wrong_final_reconciliation():
    with pytest.raises(ValueError, match='缺少'):
        build_summary(result(), {'BTC/USDT': bars([100, 120, 110])}, [])
    value = result([trade()])
    with pytest.raises(ValueError, match='最终现金'):
        build_summary(value, prices(), [])


def test_mismatched_fee_and_wallet_chronology_rejected():
    value = result([trade()]); value['final_balance'] = 1009.79; value['fee'] = .002
    with pytest.raises(ValueError, match='费率'):
        build_summary(value, prices(), [])
    value['fee'] = .001
    with pytest.raises(ValueError, match='时间'):
        build_summary(value, prices(), [(START + DAY, 1000), (START, 1000)])
    with pytest.raises(ValueError, match='缺口'):
        build_summary(value, prices(), [(START, 1000), (START + 2 * DAY, 1009.79)])


@pytest.mark.parametrize('rows', [[], [[START, 1]],
    bars([100, 120, 110])[:1] + [[START + DAY, 120, 100, 120, 120, 1]],
    [[START, 100, 100, 100, 100, -1]], [[float('nan'), 1, 1, 1, 1, 1]]])
def test_bad_candle_shapes_and_prices(rows):
    with pytest.raises(ValueError):
        validate_bars(rows, START, START + 2 * DAY)


def test_empty_wallet_prepends_capital_when_first_snapshot_is_next_bar():
    value = result([trade()]); value['final_balance'] = 1009.79
    summary = build_summary(value, prices(), [(START + DAY, 1020), (START + 2 * DAY, 1010)])
    assert summary['equity'][0] == (START, 1000)


def test_readonly_archive_parser_requires_unique_result_and_fee(tmp_path):
    archive = tmp_path / 'bad.zip'
    with ZipFile(archive, 'w') as z:
        z.writestr('unrelated.json', '{}')
    with pytest.raises(ValueError, match='唯一'):
        report.load_result(archive, 'S1TrendFollow')
    value = result(); del value['fee']
    with ZipFile(archive, 'w') as z:
        z.writestr('engine.json', json.dumps({'strategy': {'S1TrendFollow': value}}))
    with pytest.raises(ValueError, match='手续费'):
        report.load_result(archive, 'S1TrendFollow')


def test_fee_can_be_inferred_from_real_trades_and_wallet_loader_is_lazy(tmp_path, monkeypatch):
    archive = tmp_path / 'engine.zip'
    value = result([trade()]); del value['fee']
    with ZipFile(archive, 'w') as z:
        z.writestr('engine.json', json.dumps({'strategy': {'S1TrendFollow': value}}))
        z.writestr('engine_S1TrendFollow_wallet.feather', b'fixture-not-executable')
    monkeypatch.setattr(report, '_wallet_rows', lambda payload: [(START, 1000)])
    loaded, wallet = report.load_result(archive, 'S1TrendFollow')
    assert loaded['fee'] == .001 and wallet == [(START, 1000)]


def test_data_loader_rejects_path_traversal_and_missing_file(tmp_path):
    with pytest.raises(ValueError):
        report.load_prices(tmp_path, result([trade(pair='../bad')]))
    with pytest.raises(ValueError, match='缺少'):
        report.load_prices(tmp_path, result())


def test_cli_success_and_failure(tmp_path, capsys):
    archive = tmp_path / 'engine.zip'
    with ZipFile(archive, 'w') as z:
        z.writestr('engine.json', json.dumps({'strategy': {'S1TrendFollow': result()}}))
    for pair, data in prices().items():
        (tmp_path / f"{pair.replace('/', '_')}-1d.json").write_text(json.dumps(data))
    argv = ['--result', str(archive), '--data-dir', str(tmp_path), '--output', str(tmp_path / 'out.html')]
    report.main(argv)
    assert '学习报告' in capsys.readouterr().out
    with pytest.raises(SystemExit) as error:
        report.main(['--result', 'missing.zip', '--data-dir', str(tmp_path), '--output', 'unused.html'])
    assert error.value.code == 2


def test_wallet_asset_values_include_unrealized_holdings(monkeypatch):
    import pandas as pd
    rows = [{'date': pd.Timestamp(START, unit='ms', tz='UTC'), 'currency': 'USDT', 'rate': 1, 'balance': 899.9},
            {'date': pd.Timestamp(START, unit='ms', tz='UTC'), 'currency': 'BTC', 'rate': 120, 'balance': 1}]
    monkeypatch.setattr(pd, 'read_feather', lambda _: pd.DataFrame(rows))
    assert report._wallet_rows(b'unused') == [(START, 1019.9)]


@pytest.mark.parametrize('kind', ['empty', 'bad_schema', 'duplicate', 'reverse', 'negative', 'nan'])
def test_wallet_export_validation(monkeypatch, kind):
    import pandas as pd
    row = {'date': pd.Timestamp(START, unit='ms', tz='UTC'), 'currency': 'USDT', 'rate': 1, 'balance': 1000}
    if kind == 'empty':
        rows = []
    elif kind == 'bad_schema':
        rows = [{'no': 'date'}]
    elif kind == 'duplicate':
        rows = [row, dict(row)]
    elif kind == 'reverse':
        rows = [dict(row, date=pd.Timestamp(START + DAY, unit='ms', tz='UTC')), row]
    elif kind == 'negative':
        rows = [dict(row, balance=-1)]
    else:
        rows = [dict(row, rate=float('nan'))]
    monkeypatch.setattr(pd, 'read_feather', lambda _: pd.DataFrame(rows))
    if kind == 'empty':
        assert report._wallet_rows(b'unused') == []
    else:
        with pytest.raises(ValueError):
            report._wallet_rows(b'unused')


def test_archive_duplicate_names_and_oversized_member(tmp_path, monkeypatch):
    archive = tmp_path / 'bad.zip'
    with pytest.warns(UserWarning), ZipFile(archive, 'w') as z:
        z.writestr('same.json', '{}'); z.writestr('same.json', '{}')
    with pytest.raises(ValueError, match='重复'):
        report.load_result(archive, 'S1TrendFollow')
    with ZipFile(archive, 'w') as z:
        z.writestr('engine.json', json.dumps({'strategy': {'S1TrendFollow': result()}}))
    monkeypatch.setattr(report, 'MAX_MEMBER_BYTES', 5)
    with pytest.raises(ValueError, match='超过'):
        report.load_result(archive, 'S1TrendFollow')


def test_four_hour_export_uses_same_accounting_and_its_own_price_files(tmp_path):
    step = 14_400_000
    value = result(); value['timeframe'] = '4h'; value['backtest_end_ts'] = START + 2 * step
    data = {pair: [[START + i * step] + row[1:] for i, row in enumerate(rows)] for pair, rows in prices().items()}
    summary = build_summary(value, data, [])
    assert len(summary['equity']) == 3 and summary['timeframe'] == '4h'
    for pair, rows in data.items():
        (tmp_path / f"{pair.replace('/', '_')}-4h.json").write_text(json.dumps(rows))
    assert report.load_prices(tmp_path, value) == data
    assert '现货四小时线' in report.render_html(summary)


def test_terminal_exit_fee_drawdown_preserves_pre_settlement_peak():
    value = result()
    market = {'BTC/USDT': bars([100, 100, 200]), 'ETH/USDT': bars([100, 100, 200])}
    summary = build_summary(value, market, [])
    assert summary['benchmark'][-1][0] == summary['benchmark'][-2][0]
    assert summary['benchmark_drawdown'] == pytest.approx(.001)


@pytest.mark.parametrize('tag,expected', [('s1_death_cross', '下穿'), ('s1_adx_weak', '减弱'), ('exit_signal', '或')])
def test_exact_s1_exit_tags_explain_signal_without_inventing_condition(tag, expected):
    value = result([trade(exit_reason=tag)]); value['final_balance'] = 1009.79
    summary = build_summary(value, prices(), [(START, 1000), (START + DAY, 1020), (START + 2 * DAY, 1010)])
    assert expected in summary['trades'][0]['exit_explanation']


@pytest.mark.parametrize('orders', [[], [{'amount': 2, 'safe_price': 100, 'ft_is_entry': True}, {'amount': 1, 'safe_price': 110, 'ft_is_entry': False}]])
def test_missing_or_inconsistent_execution_records_fail(orders):
    value = result([trade(orders=orders)]); value['final_balance'] = 1009.79
    with pytest.raises(ValueError):
        build_summary(value, prices(), [])


def test_real_freqtrade_wallet_entry_fee_is_accrued_until_force_exit():
    from pathlib import Path
    fixture = json.loads((Path(__file__).parent / 'fixtures/real_force_exit.json').read_text())
    summary = build_summary(fixture['result'], fixture['prices'], fixture['wallet'])
    entry_fee = .0264 * 1888.11 * .001
    assert summary['final'] == pytest.approx(1000.38964276)
    assert summary['equity'][0][1] == pytest.approx(fixture['result']['starting_balance'])
    assert summary['equity'][1][1] == pytest.approx(fixture['wallet'][1][1] - entry_fee)
    assert summary['equity'][-2][1] == pytest.approx(1000.49998922 - entry_fee)
    assert summary['fees_total'] == pytest.approx(.110346456)
    assert summary['equity'][-1][1] == pytest.approx(summary['initial'] + summary['profit'])


def test_entry_fee_accrual_stops_after_trade_is_closed():
    value = result([trade(close_timestamp=START + DAY)]); value['final_balance'] = 1009.79
    summary = build_summary(value, prices(), [(START, 1000), (START + DAY, 1010), (START + 2 * DAY, 1009.79)])
    assert summary['equity'] == [(START, 1000), (START + DAY, 1009.9), (START + 2 * DAY, 1009.79)]
    assert summary['profit'] == pytest.approx(9.79)


def test_same_bar_round_trip_fee_is_not_accrued_again_next_bar():
    value = result([trade(close_timestamp=START)]); value['final_balance'] = 1009.79
    summary = build_summary(value, prices(), [(START, 1000), (START + DAY, 1009.79), (START + 2 * DAY, 1009.79)])
    assert summary['equity'][1:] == [(START + DAY, 1009.79), (START + 2 * DAY, 1009.79)]


def test_long_report_collapses_trades_and_hides_raw_codes_in_optional_details():
    value = result([trade()]); value['final_balance'] = 1009.79
    summary = build_summary(value, prices(), [(START, 1000), (START + DAY, 1020), (START + 2 * DAY, 1010)])
    summary['trades'] = summary['trades'] * 49
    summary['trade_count'] = 49
    html = report.render_html(summary)
    assert '<h1>趋势跟随练习</h1>' in html
    assert html.count('<details class="trade"') == 49
    assert html.count('<details class="trade" open>') == 2
    assert html.count('<summary>核对原始记录</summary>') == 49
    assert '<p class="muted">2024-01-01 00:00 UTC → 2024-01-03 00:00 UTC · S1TrendFollow' not in html
    assert '策略标识：S1TrendFollow' in html
    assert '净收益 +9.79 USDT' in html
