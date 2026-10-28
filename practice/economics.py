"""Validate and account for exported spot trades; never generate trades."""
from datetime import datetime, timezone
from math import isfinite, isclose

DAY_MS = 86_400_000
TIMEFRAMES = {'1d': DAY_MS, '4h': 14_400_000}
MONEY_TOLERANCE = 0.02
ENTRY_REASONS = {'s1_trend_entry': '短期均线上穿长期均线，趋势强度达到策略条件',
                 'trend_entry': '短期均线上穿长期均线，趋势强度达到策略条件'}
EXIT_REASONS = {'exit_signal': '均线下穿或趋势强度减弱，详见策略条件',
                's1_death_cross': '短期均线下穿长期均线，策略要求退出',
                's1_adx_weak': '趋势强度减弱到策略设定值以下，策略要求退出',
                'stop_loss': '触及固定止损线', 'trailing_stop_loss': '触及追踪或自定义止损线',
                'roi': '达到策略设定的获利退出条件',
                'force_exit': '回测结束，框架为了结算而按最后价格卖出，并非新卖出信号'}


def number(value, name, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        raise ValueError(f'{name} 必须是有限数字')
    if positive and value <= 0:
        raise ValueError(f'{name} 必须大于零')
    return float(value)


def timestamp(value, name):
    parsed = number(value, name, positive=True)
    if parsed != int(parsed):
        raise ValueError(f'{name} 必须是毫秒整数')
    return int(parsed)


def utc_label(value):
    return datetime.fromtimestamp(value / 1000, timezone.utc).strftime('%Y-%m-%d %H:%M UTC')


def validate_bars(rows, start, end, interval=DAY_MS):
    """Require complete, chronological candles within the declared test period."""
    if not isinstance(rows, list) or not rows:
        raise ValueError('缺少行情数据')
    previous = None
    selected = []
    for row in rows:
        if not isinstance(row, list) or len(row) < 6:
            raise ValueError('行情必须是 [时间, 开, 高, 低, 收, 量]')
        stamp = timestamp(row[0], '行情时间')
        if previous is not None and stamp <= previous:
            raise ValueError('行情时间必须严格递增，不能重复')
        previous = stamp
        if start <= stamp <= end:
            for price in row[1:5]:
                number(price, '行情价格', positive=True)
            if row[2] < max(row[1], row[3], row[4]) or row[3] > min(row[1], row[2], row[4]):
                raise ValueError('行情高低价格不一致')
            if number(row[5], '成交量') < 0:
                raise ValueError('成交量不能为负数')
            selected.append(row)
    expected = list(range(start, end + 1, interval))
    if [row[0] for row in selected] != expected:
        raise ValueError('所选期间行情缺失或存在时间缺口')
    return selected


def explain_trade(trade, start, end):
    if trade.get('is_short', False) or trade.get('leverage', 1) != 1:
        raise ValueError('报告仅支持现货做多、无杠杆')
    entry, exit_ = timestamp(trade['open_timestamp'], '买入时间'), timestamp(trade['close_timestamp'], '卖出时间')
    if not start <= entry <= exit_ <= end:
        raise ValueError('交易时间超出回测区间')
    amount = number(trade['amount'], '币数量', positive=True)
    buy, sell = number(trade['open_rate'], '买入价', True), number(trade['close_rate'], '卖出价', True)
    entry_rate, exit_rate = number(trade['fee_open'], '买入费率'), number(trade['fee_close'], '卖出费率')
    if not 0 <= entry_rate < 1 or not 0 <= exit_rate < 1:
        raise ValueError('手续费率必须在 0 到 1 之间')
    fees = (amount * buy * entry_rate, amount * sell * exit_rate)
    profit = number(trade['profit_abs'], '交易净收益')
    if not isclose(amount * (sell - buy) - sum(fees), profit, abs_tol=MONEY_TOLERANCE):
        raise ValueError('交易收益与成交金额、双边手续费核对不一致；不支持分批加减仓')
    orders = trade.get('orders', [])
    if len(orders) != 2 or sum(bool(o.get('ft_is_entry')) for o in orders) != 1:
        raise ValueError('报告需要一笔买入和一笔卖出记录，不支持分批成交或加减仓')
    for order in orders:
        expected_price = buy if order.get('ft_is_entry') else sell
        if not isclose(number(order['amount'], '成交数量', True), amount) or not isclose(number(order['safe_price'], '成交价', True), expected_price, rel_tol=1e-6):
            raise ValueError('逐笔成交与交易汇总核对不一致')
    tag, reason = str(trade.get('enter_tag') or ''), str(trade.get('exit_reason') or '')
    if trade.get('is_open') and reason != 'force_exit':
        raise ValueError('真正未平仓交易缺少结束结算，不能当作现金收益')
    return dict(pair=str(trade['pair']), amount=amount, open_rate=buy, close_rate=sell,
                open_ts=entry, close_ts=exit_, entry_fee=fees[0], exit_fee=fees[1], profit=profit,
                entry_tag=tag, exit_tag=reason, held_until_end=reason == 'force_exit',
                entry_explanation=ENTRY_REASONS.get(tag, '入场标签未提供或未知；请查看策略条件'),
                exit_explanation=EXIT_REASONS.get(reason, '未知退出原因，请对照原始框架标签'))


def _terminal_delta(trades, price_map, end):
    """Adjust pre-action last wallet snapshot for recorded last-bar executions."""
    delta = 0.0
    for trade in trades:
        if trade['open_ts'] == end:
            delta -= trade['entry_fee']
        if trade['close_ts'] == end:
            mark = trade['open_rate'] if trade['open_ts'] == end else price_map[trade['pair']][-1][1]
            delta += trade['amount'] * (trade['close_rate'] - mark) - trade['exit_fee']
    return delta


def _equity_curve(raw, start, end, initial, final, trades, prices, interval):
    if not raw:
        if trades:
            raise ValueError('有交易但缺少框架钱包市值快照，不能把已平仓利润冒充总资产')
        return [(t, initial) for t in range(start, end + 1, interval)]
    curve = [(timestamp(t, '钱包时间'), number(v, '钱包市值', True)) for t, v in raw]
    times = [t for t, _ in curve]
    if times != sorted(set(times)) or times[-1] != end or times[0] not in (start, start + interval):
        raise ValueError('钱包快照时间顺序或回测边界不匹配')
    if any(b - a != interval for a, b in zip(times, times[1:])):
        raise ValueError('钱包市值快照存在缺口')
    # FT 2026.6 books both fees only on closure. Accrue the entry fee
    # while the trade exists at this pre-action snapshot, exactly once.
    curve = [(t, number(v - sum(trade['entry_fee'] for trade in trades
              if trade['open_ts'] < t <= trade['close_ts']), '扣费后钱包市值', True))
             for t, v in curve]
    if curve[0][0] > start:
        curve.insert(0, (start, initial))
    expected = curve[-1][1] + _terminal_delta(trades, prices, end)
    if not isclose(expected, final, abs_tol=MONEY_TOLERANCE):
        raise ValueError(f'框架钱包末值与最终余额核对不一致：{expected:.6f} / {final:.6f}')
    if not isclose(curve[-1][1], final, abs_tol=1e-10):
        curve.append((end, final))
    return curve


def _drawdown(curve, initial):
    peak, maximum = initial, 0.0
    for _, value in curve:
        peak = max(peak, value)
        maximum = max(maximum, (peak - value) / peak)
    return maximum


def _benchmark(prices, initial, fee):
    """Equal capital BTC/ETH: entry fee on first open, exit fee on last open."""
    names = ('BTC/USDT', 'ETH/USDT')
    amounts = {pair: initial / 2 / (prices[pair][0][1] * (1 + fee)) for pair in names}
    curve = []
    for index, row in enumerate(prices[names[0]]):
        value = sum(amounts[pair] * prices[pair][index][1] for pair in names)
        curve.append((row[0], value))
    curve.append((curve[-1][0], curve[-1][1] * (1 - fee)))
    return curve


def build_summary(result, ohlcv, wallet):
    if result.get('trading_mode') != 'spot' or result.get('timeframe') not in TIMEFRAMES:
        raise ValueError('本练习报告仅支持现货日线或四小时线')
    if result.get('stake_currency') != 'USDT':
        raise ValueError('本练习仅支持 USDT 计价')
    start, end = timestamp(result['backtest_start_ts'], '开始时间'), timestamp(result['backtest_end_ts'], '结束时间')
    interval = TIMEFRAMES[result['timeframe']]
    if end <= start or start % interval or (end - start) % interval:
        raise ValueError('回测区间必须按完整行情周期对齐')
    initial, final = number(result['starting_balance'], '起始资金', True), number(result['final_balance'], '结束资金', True)
    fee = number(result['fee'], '比较基准费率')
    if not 0 <= fee < 1:
        raise ValueError('比较基准费率不合法')
    trades = [explain_trade(t, start, end) for t in result['trades']]
    pairs = {'BTC/USDT', 'ETH/USDT'} | {t['pair'] for t in trades}
    if pairs - set(ohlcv):
        raise ValueError('缺少所需币种行情数据')
    prices = {pair: validate_bars(ohlcv[pair], start, end, interval) for pair in pairs}
    if not isclose(initial + sum(t['profit'] for t in trades), final, abs_tol=MONEY_TOLERANCE):
        raise ValueError('最终现金余额与逐笔净收益核对不一致')
    if any(not isclose(t['entry_fee'] / (t['amount'] * t['open_rate']), fee) or
           not isclose(t['exit_fee'] / (t['amount'] * t['close_rate']), fee) for t in trades):
        raise ValueError('交易费率与基准费率不一致')
    equity = _equity_curve(wallet, start, end, initial, final, trades, prices, interval)
    benchmark = _benchmark(prices, initial, fee)
    return dict(strategy=str(result['strategy_name']), start=start, end=end, initial=initial, final=final,
                timeframe=result['timeframe'], profit=final-initial, profit_ratio=(final-initial)/initial, fee=fee,
                fees_total=sum(t['entry_fee'] + t['exit_fee'] for t in trades), trade_count=len(trades),
                forced_close_count=sum(t['held_until_end'] for t in trades), trades=trades,
                terminal_holdings_value=sum(t['amount'] * t['close_rate'] for t in trades if t['held_until_end']),
                terminal_unrealized=sum(t['profit'] + t['exit_fee'] for t in trades if t['held_until_end']),
                equity=equity, drawdown=_drawdown(equity, initial), benchmark=benchmark,
                benchmark_final=benchmark[-1][1], benchmark_drawdown=_drawdown(benchmark, initial),
                stake_amount=result.get('stake_amount'), equity_source='Freqtrade 钱包每根行情线开盘市值快照，补扣持仓买入费用，末点按导出结算核对')
