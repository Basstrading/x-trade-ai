"""Test News Trading Strategy sur 5 ans."""
import sys
from pathlib import Path
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backtester.news_engine import NewsBacktestEngine

BASE_DIR = Path(__file__).resolve().parent.parent
df = pd.read_csv(BASE_DIR / 'data' / 'databento_nq_5min_5y.csv',
                 index_col=0, parse_dates=True)
PV = 8.0

print("Data: {} bars".format(len(df)))
print()

def run_config(name, **kwargs):
    engine = NewsBacktestEngine(point_value=PV, **kwargs)
    report = engine.run(df)
    if report and report.total_trades >= 5:
        tdf = pd.DataFrame(report.trades)
        tdf['dp'] = pd.to_datetime(tdf['date'])
        tdf['month'] = tdf['dp'].dt.to_period('M')
        mp = sum(1 for _, g in tdf.groupby('month') if g['pnl_usd'].sum() > 0)
        mn = sum(1 for _, g in tdf.groupby('month') if g['pnl_usd'].sum() <= 0)
        # Direction stats
        long_t = tdf[tdf['direction'] == 'long']
        short_t = tdf[tdf['direction'] == 'short']
        # Exit reasons
        exits = {}
        for reason, g in tdf.groupby('exit_reason'):
            exits[reason] = len(g)
        return dict(name=name, trades=report.total_trades, wr=report.win_rate,
                    pnl=report.total_pnl_usd, pf=report.profit_factor,
                    sharpe=report.sharpe_ratio, max_dd=report.max_drawdown_usd,
                    avg_win=report.avg_win, avg_loss=report.avg_loss,
                    mp=mp, mn=mn, long_n=len(long_t), short_n=len(short_t),
                    exits=exits)
    return None

# ============================================================
# TEST 1: Tier 1 only (NFP, CPI, FOMC) — different configs
# ============================================================
print('=' * 140)
print('  TEST 1: TIER 1 SEULEMENT (NFP, CPI, FOMC)')
print('=' * 140)

configs = [
    # (name, kwargs)
    ('base: lb30 sl150 tp200 hold60',     dict(lookback_min=30, min_move_pts=10, entry_before_min=5, wide_sl_pts=150, tp_pts=200, max_hold_min=60, tier_filter=1)),
    ('lb60 sl150 tp200 hold60',           dict(lookback_min=60, min_move_pts=15, entry_before_min=5, wide_sl_pts=150, tp_pts=200, max_hold_min=60, tier_filter=1)),
    ('lb30 sl100 tp150 hold60',           dict(lookback_min=30, min_move_pts=10, entry_before_min=5, wide_sl_pts=100, tp_pts=150, max_hold_min=60, tier_filter=1)),
    ('lb30 sl200 tp300 hold60',           dict(lookback_min=30, min_move_pts=10, entry_before_min=5, wide_sl_pts=200, tp_pts=300, max_hold_min=60, tier_filter=1)),
    ('lb30 sl150 tp200 hold120',          dict(lookback_min=30, min_move_pts=15, entry_before_min=5, wide_sl_pts=150, tp_pts=200, max_hold_min=120, tier_filter=1)),
    ('lb30 sl150 tp200 trail10',          dict(lookback_min=30, min_move_pts=10, entry_before_min=5, wide_sl_pts=150, tp_pts=200, max_hold_min=60, trail_bars=10, tier_filter=1)),
    ('lb30 sl150 tp300 hold120',          dict(lookback_min=30, min_move_pts=10, entry_before_min=5, wide_sl_pts=150, tp_pts=300, max_hold_min=120, tier_filter=1)),
    ('lb15 sl100 tp150 hold30',           dict(lookback_min=15, min_move_pts=5, entry_before_min=3, wide_sl_pts=100, tp_pts=150, max_hold_min=30, tier_filter=1)),
    ('lb30 sl150 tp200 entry2',           dict(lookback_min=30, min_move_pts=10, entry_before_min=2, wide_sl_pts=150, tp_pts=200, max_hold_min=60, tier_filter=1)),
    ('lb30 sma direction',                dict(lookback_min=30, min_move_pts=10, entry_before_min=5, wide_sl_pts=150, tp_pts=200, max_hold_min=60, tier_filter=1, use_sma_direction=True)),
    ('lb30 min_move=20',                  dict(lookback_min=30, min_move_pts=20, entry_before_min=5, wide_sl_pts=150, tp_pts=200, max_hold_min=60, tier_filter=1)),
    ('lb30 min_move=30',                  dict(lookback_min=30, min_move_pts=30, entry_before_min=5, wide_sl_pts=150, tp_pts=200, max_hold_min=60, tier_filter=1)),
    ('lb60 min_move=30 sl200 tp300',      dict(lookback_min=60, min_move_pts=30, entry_before_min=5, wide_sl_pts=200, tp_pts=300, max_hold_min=90, tier_filter=1)),
]

results = []
for name, kwargs in configs:
    r = run_config(name, **kwargs)
    if r:
        results.append(r)

print()
print("  {:<40} {:>5} {:>6} {:>11} {:>5} {:>6} {:>8} {:>5} {:>4}/{:<4} {:>8} {:>8}  exits".format(
    'Config', 'Tr', 'WR', 'PnL', 'PF', 'Sharp', 'MaxDD', 'M+', 'L', 'S', 'AvgWin', 'AvgLoss'))
print('  ' + '-' * 130)
for r in sorted(results, key=lambda x: x['pnl'], reverse=True):
    tm = r['mp'] + r['mn']
    ex = ' '.join('{}={}'.format(k, v) for k, v in sorted(r['exits'].items()))
    print("  {:<40} {:>5} {:>5.1f}% ${:>+9,.0f} {:>5.2f} {:>5.2f} ${:>7,.0f} {}/{} {:>4}/{:<4} ${:>+6,.0f} ${:>+6,.0f}  {}".format(
        r['name'], r['trades'], r['wr'], r['pnl'], r['pf'], r['sharpe'], r['max_dd'],
        r['mp'], tm, r['long_n'], r['short_n'], r['avg_win'], r['avg_loss'], ex))

# ============================================================
# TEST 2: Tier 1+2 (all major events)
# ============================================================
print()
print('=' * 140)
print('  TEST 2: TIER 1+2 (NFP, CPI, FOMC + PPI, ISM, GDP, Retail)')
print('=' * 140)

configs2 = [
    ('t1+2 lb30 sl150 tp200 hold60',     dict(lookback_min=30, min_move_pts=10, entry_before_min=5, wide_sl_pts=150, tp_pts=200, max_hold_min=60, tier_filter=2)),
    ('t1+2 lb60 sl150 tp200 hold60',     dict(lookback_min=60, min_move_pts=15, entry_before_min=5, wide_sl_pts=150, tp_pts=200, max_hold_min=60, tier_filter=2)),
    ('t1+2 lb30 sl200 tp300 hold90',     dict(lookback_min=30, min_move_pts=10, entry_before_min=5, wide_sl_pts=200, tp_pts=300, max_hold_min=90, tier_filter=2)),
    ('t1+2 lb30 min_move=20',            dict(lookback_min=30, min_move_pts=20, entry_before_min=5, wide_sl_pts=150, tp_pts=200, max_hold_min=60, tier_filter=2)),
    ('t1+2 lb60 min_move=30 sl200 tp300', dict(lookback_min=60, min_move_pts=30, entry_before_min=5, wide_sl_pts=200, tp_pts=300, max_hold_min=90, tier_filter=2)),
]

results2 = []
for name, kwargs in configs2:
    r = run_config(name, **kwargs)
    if r:
        results2.append(r)

print()
print("  {:<40} {:>5} {:>6} {:>11} {:>5} {:>6} {:>8} {:>5} {:>4}/{:<4} {:>8} {:>8}  exits".format(
    'Config', 'Tr', 'WR', 'PnL', 'PF', 'Sharp', 'MaxDD', 'M+', 'L', 'S', 'AvgWin', 'AvgLoss'))
print('  ' + '-' * 130)
for r in sorted(results2, key=lambda x: x['pnl'], reverse=True):
    tm = r['mp'] + r['mn']
    ex = ' '.join('{}={}'.format(k, v) for k, v in sorted(r['exits'].items()))
    print("  {:<40} {:>5} {:>5.1f}% ${:>+9,.0f} {:>5.2f} {:>5.2f} ${:>7,.0f} {}/{} {:>4}/{:<4} ${:>+6,.0f} ${:>+6,.0f}  {}".format(
        r['name'], r['trades'], r['wr'], r['pnl'], r['pf'], r['sharpe'], r['max_dd'],
        r['mp'], tm, r['long_n'], r['short_n'], r['avg_win'], r['avg_loss'], ex))

print()
print('=' * 140)
