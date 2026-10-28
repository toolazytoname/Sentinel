"""CLI: turn real Freqtrade exports into a fully offline Chinese report."""
import argparse
from io import BytesIO
import json
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from practice.economics import build_summary, number
from practice.presentation import render_html

MAX_MEMBER_BYTES = 100_000_000


def _read_member(archive, name):
    if archive.getinfo(name).file_size > MAX_MEMBER_BYTES:
        raise ValueError('回测文件成员超过 100 MB，拒绝加载')
    return archive.read(name)


def _wallet_rows(payload):
    """Import Arrow/pandas lazily: ordinary project tests need neither package."""
    import pandas as pd
    frame = pd.read_feather(BytesIO(payload))
    if frame.empty:
        return []
    if not {'date', 'currency', 'rate', 'balance'} <= set(frame.columns):
        raise ValueError('不支持的钱包快照格式，需要 Freqtrade 2026.6')
    totals, seen, previous = {}, set(), None
    for row in frame.to_dict('records'):
        stamp = int(pd.Timestamp(row['date']).timestamp() * 1000)
        key = (stamp, str(row['currency']))
        if key in seen or (previous is not None and stamp < previous):
            raise ValueError('钱包快照重复或时间倒序')
        seen.add(key)
        previous = stamp
        balance, rate = number(row['balance'], '钱包持有量'), number(row['rate'], '钱包价格', True)
        if balance < 0:
            raise ValueError('钱包持有量不能为负数')
        totals[stamp] = totals.get(stamp, 0) + rate * balance
    return list(totals.items())


def load_result(path, strategy):
    with ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError('ZIP 存在重复文件名')
        results = []
        for name in names:
            if name.endswith('.json') and not name.endswith(('_config.json', '.meta.json')):
                data = json.loads(_read_member(archive, name))
                if isinstance(data, dict) and isinstance(data.get('strategy'), dict):
                    results.append((name, data['strategy']))
        if len(results) != 1 or strategy not in results[0][1]:
            raise ValueError('找不到唯一的目标策略回测结果')
        name, strategies = results[0]
        result = dict(strategies[strategy])
        result['strategy_name'] = strategy
        config_name = name[:-5] + '_config.json'
        config = json.loads(_read_member(archive, config_name)) if config_name in names else {}
        fees = {t['fee_open'] for t in result['trades']} | {t['fee_close'] for t in result['trades']}
        if 'fee' not in result:
            if 'fee' in config:
                result['fee'] = config['fee']
            elif len(fees) == 1:
                result['fee'] = fees.pop()
            else:
                raise ValueError('无法确定手续费；零交易时导出配置必须明确设置 fee')
        wallet_name = name[:-5] + '_' + strategy + '_wallet.feather'
        wallet = _wallet_rows(_read_member(archive, wallet_name)) if wallet_name in names else []
    return result, wallet


def load_prices(data_dir, result):
    pairs = {'BTC/USDT', 'ETH/USDT'} | {str(t['pair']) for t in result['trades']}
    data = {}
    for pair in pairs:
        if '/' not in pair or any(c not in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789/' for c in pair):
            raise ValueError('不支持的现货币对名称')
        path = Path(data_dir) / (pair.replace('/', '_') + '-' + result['timeframe'] + '.json')
        if not path.is_file():
            raise ValueError(f'缺少本地行情文件：{path.name}')
        data[pair] = json.loads(path.read_text())
    return data


def generate_report(result_path, data_dir, output, strategy='S1TrendFollow'):
    result, wallet = load_result(result_path, strategy)
    summary = build_summary(result, load_prices(data_dir, result), wallet)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_html(summary), encoding='utf-8')
    compact = {key: value for key, value in summary.items() if key not in ('trades', 'equity', 'benchmark')}
    output.with_suffix('.json').write_text(json.dumps(compact, ensure_ascii=False, indent=2), encoding='utf-8')
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description='读取 Freqtrade 回测，生成离线中文学习报告')
    parser.add_argument('--result', type=Path, required=True)
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--strategy', default='S1TrendFollow')
    args = parser.parse_args(argv)
    try:
        summary = generate_report(args.result, args.data_dir, args.output, args.strategy)
    except (ValueError, KeyError, OSError, ImportError, BadZipFile) as error:
        parser.exit(2, f'报告未生成：{error}\n')
    print(f'学习报告：{args.output.resolve()}\n交易 {summary["trade_count"]} 笔，净收益 {summary["profit"]:.2f} USDT')


if __name__ == '__main__':
    main()
