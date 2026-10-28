"""Self-contained, mobile-friendly HTML without remote assets or trading controls."""
from html import escape
from practice.economics import utc_label

CSS = '''
:root{color-scheme:light;--paper:#f6f3ed;--ink:#292e2b;--muted:#6c746e;--line:#dedfd5;--green:#326855;--orange:#b07832}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft Yahei",sans-serif;line-height:1.65}main{max-width:1100px;margin:auto;padding:48px 28px 64px}header{border-bottom:1px solid var(--line);padding-bottom:24px}h1{font-size:clamp(28px,5vw,44px);font-weight:650;letter-spacing:-1px;margin:8px 0}h2{font-size:22px;margin:0 0 16px}p{margin:8px 0}.eyebrow{font-size:12px;letter-spacing:2px;color:var(--green)}.muted,small{color:var(--muted)}.badge{display:inline-block;background:#e8ecdf;color:var(--green);padding:3px 10px;border-radius:20px;font-size:13px}.cards{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:26px 0}.card,section{background:#fffdf9;border:1px solid var(--line);border-radius:14px;padding:22px}.card span{display:block;font-size:13px;color:var(--muted)}.card strong{display:block;font-size:27px;margin-top:8px;font-weight:600;font-variant-numeric:tabular-nums}.card small{font-size:12px}section{margin:18px 0}.legend{display:flex;gap:20px;flex-wrap:wrap;font-size:13px}.legend i{display:inline-block;width:20px;height:3px;vertical-align:middle;margin-right:6px;background:var(--green)}.legend .b{background:var(--orange)}.chart{width:100%;height:auto;display:block;margin:14px 0}.note{border-left:3px solid #ccbc98;padding:10px 15px;background:#f3f0e8;font-size:14px}.table-scroll{overflow-x:auto}table{width:100%;border-collapse:collapse;font-size:14px}th{text-align:left;color:var(--muted);font-size:12px;font-weight:500}td,th{padding:13px 10px;border-bottom:1px solid var(--line);vertical-align:top}td:first-child,th:first-child{padding-left:0}td.n{white-space:nowrap;font-variant-numeric:tabular-nums}.trade{margin-top:18px;border-top:1px solid var(--line);padding-top:18px}summary{cursor:pointer}.trade>summary{font-size:16px;padding:5px 0}.trade-meta{display:block;margin:3px 0 8px 18px;font-size:13px;color:var(--muted)}.raw-records{margin-top:12px;color:var(--muted);font-size:13px}.trade-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px 28px}.code{font-family:ui-monospace,monospace;font-size:12px;word-break:break-all}footer{font-size:12px;color:var(--muted);margin-top:26px}.up{color:var(--green)}.down{color:#a0473f}@media(max-width:680px){main{padding:26px 16px}.cards{grid-template-columns:repeat(2,1fr);gap:10px}.card,section{padding:16px}.card strong{font-size:24px}.trade-grid{grid-template-columns:1fr}td,th{padding:10px 7px}}
'''


def _money(value):
    return f'{value:,.2f}'


def _percent(value):
    return f'{value * 100:.2f}%'


def _chart(summary):
    curves = (summary['equity'], summary['benchmark'])
    values = [v for curve in curves for _, v in curve] + [summary['initial']]
    low, high = min(values), max(values)
    span = max(high - low, summary['initial'] * .04)
    low, high = low - span * .12, high + span * .12
    start, end = summary['start'], summary['end']
    x = lambda t: 72 + (t-start)/(end-start)*858
    y = lambda v: 28 + (high-v)/(high-low)*260
    svg = ['<svg class="chart" viewBox="0 0 960 340" role="img" aria-label="策略总资产与买入持有资产对比">']
    for i in range(5):
        value = low + (high-low)*i/4
        svg.append(f'<line x1="72" x2="930" y1="{y(value):.1f}" y2="{y(value):.1f}" stroke="#e4e5dd"/><text x="62" y="{y(value)+4:.1f}" text-anchor="end" font-size="12" fill="#6c746e">{value:,.0f}</text>')
    for curve, color in zip(curves, ('#326855', '#b07832')):
        points = ' '.join(f'{x(t):.2f},{y(v):.2f}' for t, v in curve)
        svg.append(f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="2.5" stroke-linejoin="round"/>')
    for t, anchor in ((start, 'start'), (end, 'end')):
        svg.append(f'<text x="{x(t):.0f}" y="322" text-anchor="{anchor}" font-size="12" fill="#6c746e">{utc_label(t)[:10]}</text>')
    return ''.join(svg) + '</svg>'


def _cards(s):
    cards = [('开始的钱', _money(s['initial']), 'USDT · 虚拟资金'),
             ('结束的钱', _money(s['final']), 'USDT · 已含手续费'),
             ('净赚 / 净亏', f'{s["profit"]:+,.2f}', _percent(s['profit_ratio'])),
             ('期间最大回撤', _percent(s['drawdown']), '从资产高点到后续低点的跌幅')]
    return '<div class="cards">' + ''.join(f'<div class="card"><span>{label}</span><strong>{value}</strong><small>{hint}</small></div>' for label, value, hint in cards) + '</div>'


def _comparison(s):
    rows = [('这套策略', s['final'], s['drawdown'], f'共 {s["trade_count"]} 笔买卖；每笔设定 {escape(str(s["stake_amount"]))} USDT'),
            ('各买一半 BTC / ETH 并持有', s['benchmark_final'], s['benchmark_drawdown'], '第一天全部投入，各占一半；末日模拟卖出')]
    body = ''.join(f'<tr><td>{name}<br><small>{note}</small></td><td class="n">{_money(final)}</td><td class="n">{_percent((final-s["initial"])/s["initial"])}</td><td class="n">{_percent(dd)}</td></tr>' for name, final, dd, note in rows)
    return '<div class="table-scroll"><table><thead><tr><th>怎么做</th><th>结束资产 USDT</th><th>收益率</th><th>最大回撤</th></tr></thead><tbody>' + body + '</tbody></table></div>'


def _trade_block(t, index):
    held = '<span class="badge">一直持有到回测结束</span>' if t['held_until_end'] else ''
    expanded = ' open' if index <= 2 else ''
    dates = f"{utc_label(t['open_ts'])[:10]} → {utc_label(t['close_ts'])[:10]}"
    return f'''<details class="trade"{expanded}><summary><strong>{index}. {escape(t['pair'])}</strong> {held}<span class="trade-meta">{dates} · 净收益 {t['profit']:+,.2f} USDT</span></summary>
<div class="trade-grid"><div><small>买入 · {utc_label(t['open_ts'])}</small><p>以 {_money(t['open_rate'])} USDT 买入 {t['amount']:.8g} 个币。</p><p>{escape(t['entry_explanation'])}</p></div>
<div><small>卖出 / 结束结算 · {utc_label(t['close_ts'])}</small><p>以 {_money(t['close_rate'])} USDT 卖出 {t['amount']:.8g} 个币。</p><p>{escape(t['exit_explanation'])}</p></div></div>
<p>买入金额 {_money(t['amount']*t['open_rate'])}；卖出金额 {_money(t['amount']*t['close_rate'])} USDT。</p><p>手续费：买入 {_money(t['entry_fee'])} + 卖出 {_money(t['exit_fee'])} = {_money(t['entry_fee']+t['exit_fee'])} USDT。扣费后净收益 <strong>{t['profit']:+,.2f} USDT</strong>。</p>
<details class="raw-records"><summary>核对原始记录</summary><p class="code">买入标签：{escape(t['entry_tag'] or '（无）')}</p><p class="code">卖出标签：{escape(t['exit_tag'])}</p></details></details>'''


def render_html(s):
    trades = ''.join(_trade_block(t, i) for i, t in enumerate(s['trades'], 1))
    if not trades:
        trades = '<p>这段时间没有交易。策略一直持有现金，收益和手续费均为 0。这也是需要如实记录的结果。</p>'
    sampling = '日线' if s['timeframe'] == '1d' else '四小时线'
    holdings = ''
    if s['forced_close_count']:
        holdings = f'<p class="note">结束结算前仍持有的币，市值合计 {_money(s["terminal_holdings_value"])} USDT；这部分尚未卖出的盈亏为 {s["terminal_unrealized"]:+,.2f} USDT（已扣买入费，未扣最后卖出费）。框架随后模拟卖出，计入上面的结束余额。</p>'
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>我的量化练习 · 历史回放</title><style>{CSS}</style></head><body><main>
<header><div class="eyebrow">SENTINEL / 我的第一份交易练习</div><h1>趋势跟随练习</h1><p>用真实历史价格和虚拟资金，逐笔看懂买卖。</p><p class="muted">{utc_label(s['start'])} → {utc_label(s['end'])} · 现货{sampling}</p><span class="badge">历史回测 · 未投入真实资金</span></header>
{_cards(s)}<section><h2>钱是怎样变化的</h2><div class="legend"><span><i></i>策略总资产</span><span><i class="b"></i>一半 BTC + 一半 ETH 买入持有</span></div>{_chart(s)}{_comparison(s)}
<p class="note">两边开始的钱和时间相同。持有方案第一天全部买入；策略按固定金额逐笔买，可能有很多钱闲置。资金投入程度不同，不能只凭最后谁赚得多就判断策略好坏。</p>
<p class="muted">总资产 = 现金 + 当时持有币的市值，包含持有期间尚未实现的盈亏。不是只把已卖出的利润累加。持仓期间已补扣买入费用，平仓后不重复扣。数据来自 {escape(s['equity_source'])}；按行情周期取样，无法代表周期内最深跌幅。</p></section>
<section><h2>买卖了什么，为什么买卖</h2><p>共 <strong>{s['trade_count']}</strong> 笔完整交易；双边手续费合计 <strong>{_money(s['fees_total'])} USDT</strong>；其中 <strong>{s['forced_close_count']}</strong> 笔持有到回测结束。</p>{holdings}{trades}</section>
<section><h2>这份练习怎样计算</h2><details class="raw-records"><summary>核对策略标识</summary><p class="code">策略标识：{escape(s['strategy'])}</p></details><p>买入持有：开始时按开盘价各买一半 BTC、ETH；买入手续费从本金中扣除；结束时按末日开盘价模拟卖出，再扣卖出手续费。单边费率 {_percent(s['fee'])}，与策略一致。</p><p>结束时仍持有的仓位，Freqtrade 会为了回测结算强制卖出，费用计入结果。强制卖出不表示策略真的发出了卖出信号。终点显示结算现金；之前的曲线保留持有期间的未实现盈亏。</p><p>本报告仅支持现货做多、无杠杆、每笔一次买入和一次卖出。原始原因标签未知时如实标记，不编造解释。</p><p class="note">历史回放让你理解规则，不保证未来挣钱。它与跟随当前行情运行的实时模拟（dry-run）不同；这份报告没有启动实时机器人。</p></section>
<footer>可离线打开 · 没有账户密钥 · 没有下单入口 · 金额单位 USDT，显示保留两位小数，计算使用原始精度。</footer></main></body></html>'''
