"""News Trading V2: NFP+CPI only, min_move>=30, no FOMC."""
import sys
from pathlib import Path
import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backtester.news_engine import NewsBacktestEngine

BASE_DIR = Path(__file__).resolve().parent.parent
df = pd.read_csv(BASE_DIR / 'data' / 'databento_nq_5min_5y.csv',
                 index_col=0, parse_dates=True)
PV = 8.0

# Create filtered calendar: NFP + CPI only (no FOMC)
cal = pd.read_csv(BASE_DIR / 'data' / 'news_calendar_clean.csv')
cal_filtered = cal[cal['events'].str.contains('Non-Farm|CPI|Unemployment Rate|Average Hourly', case=False)]
# Exclude anything with FOMC
cal_filtered = cal_filtered[~cal_filtered['events'].str.contains('FOMC|Federal Funds', case=False)]
cal_filtered.to_csv(BASE_DIR / 'data' / 'news_calendar_nfp_cpi.csv', index=False)
print("Calendar NFP+CPI: {} events".format(len(cal_filtered)))
print()

def run_config(name, **kwargs):
    engine = NewsBacktestEngine(point_value=PV, **kwargs)
    report = engine.run(df, calendar_path=str(BASE_DIR / 'data' / 'news_calendar_nfp_cpi.csv'))
    if report and report.total_trades >= 3:
        tdf = pd.DataFrame(report.trades)
        tdf['dp'] = pd.to_datetime(tdf['date'])
        tdf['month'] = tdf['dp'].dt.to_period('M')
        tdf['year'] = tdf['dp'].dt.year
        mp = sum(1 for _, g in tdf.groupby('month') if g['pnl_usd'].sum() > 0)
        mn = sum(1 for _, g in tdf.groupby('month') if g['pnl_usd'].sum() <= 0)
        long_t = tdf[tdf['direction'] == 'long']
        short_t = tdf[tdf['direction'] == 'short']
        exits = {}
        for reason, g in tdf.groupby('exit_reason'):
            exits[reason] = len(g)
        yearly = {}
        for y, g in tdf.groupby('year'):
            yearly[int(y)] = round(g['pnl_usd'].sum(), 0)
        return dict(name=name, trades=report.total_trades, wr=report.win_rate,
                    pnl=report.total_pnl_usd, pf=report.profit_factor,
                    sharpe=report.sharpe_ratio, max_dd=report.max_drawdown_usd,
                    avg_win=report.avg_win, avg_loss=report.avg_loss,
                    mp=mp, mn=mn, long_n=len(long_t), short_n=len(short_t),
                    exits=exits, yearly=yearly, report=report)
    return None

# === GRID: different configs with min_move=30 ===
print('=' * 140)
print('  NFP+CPI ONLY | min_move>=30 | NO FOMC')
print('=' * 140)

configs = [
    ('mm30 sl100 tp150 h30',    dict(lookback_min=30, min_move_pts=30, entry_before_min=2, wide_sl_pts=100, tp_pts=150, max_hold_min=30, tier_filter=3)),
    ('mm30 sl100 tp150 h60',    dict(lookback_min=30, min_move_pts=30, entry_before_min=2, wide_sl_pts=100, tp_pts=150, max_hold_min=60, tier_filter=3)),
    ('mm30 sl100 tp200 h60',    dict(lookback_min=30, min_move_pts=30, entry_before_min=2, wide_sl_pts=100, tp_pts=200, max_hold_min=60, tier_filter=3)),
    ('mm30 sl150 tp200 h60',    dict(lookback_min=30, min_move_pts=30, entry_before_min=2, wide_sl_pts=150, tp_pts=200, max_hold_min=60, tier_filter=3)),
    ('mm30 sl150 tp300 h60',    dict(lookback_min=30, min_move_pts=30, entry_before_min=2, wide_sl_pts=150, tp_pts=300, max_hold_min=60, tier_filter=3)),
    ('mm30 sl150 tp200 h90',    dict(lookback_min=30, min_move_pts=30, entry_before_min=2, wide_sl_pts=150, tp_pts=200, max_hold_min=90, tier_filter=3)),
    ('mm30 sl200 tp300 h90',    dict(lookback_min=30, min_move_pts=30, entry_before_min=2, wide_sl_pts=200, tp_pts=300, max_hold_min=90, tier_filter=3)),
    ('mm30 sl100 tp150 h60 tr8', dict(lookback_min=30, min_move_pts=30, entry_before_min=2, wide_sl_pts=100, tp_pts=150, max_hold_min=60, trail_bars=8, tier_filter=3)),
    ('mm30 sl150 tp200 h60 tr10', dict(lookback_min=30, min_move_pts=30, entry_before_min=2, wide_sl_pts=150, tp_pts=200, max_hold_min=60, trail_bars=10, tier_filter=3)),
    # Lookback 60 min
    ('lb60 mm30 sl150 tp200 h60', dict(lookback_min=60, min_move_pts=30, entry_before_min=2, wide_sl_pts=150, tp_pts=200, max_hold_min=60, tier_filter=3)),
    ('lb60 mm30 sl100 tp150 h60', dict(lookback_min=60, min_move_pts=30, entry_before_min=2, wide_sl_pts=100, tp_pts=150, max_hold_min=60, tier_filter=3)),
    # min_move=20 pour comparaison
    ('mm20 sl150 tp200 h60',    dict(lookback_min=30, min_move_pts=20, entry_before_min=2, wide_sl_pts=150, tp_pts=200, max_hold_min=60, tier_filter=3)),
    ('mm20 sl100 tp150 h60',    dict(lookback_min=30, min_move_pts=20, entry_before_min=2, wide_sl_pts=100, tp_pts=150, max_hold_min=60, tier_filter=3)),
    # Entry 5min before
    ('mm30 sl150 tp200 h60 e5',  dict(lookback_min=30, min_move_pts=30, entry_before_min=5, wide_sl_pts=150, tp_pts=200, max_hold_min=60, tier_filter=3)),
]

results = []
for name, kwargs in configs:
    r = run_config(name, **kwargs)
    if r:
        results.append(r)

print()
print("  {:<30} {:>4} {:>6} {:>10} {:>5} {:>6} {:>8} {:>4}/{:<4} {:>7} {:>7}  exits".format(
    'Config', 'Tr', 'WR', 'PnL', 'PF', 'Sharp', 'MaxDD', 'L', 'S', 'AvgWin', 'AvgLos'))
print('  ' + '-' * 115)
for r in sorted(results, key=lambda x: x['pnl'], reverse=True):
    ex = ' '.join('{}={}'.format(k, v) for k, v in sorted(r['exits'].items()))
    print("  {:<30} {:>4} {:>5.1f}% ${:>+8,.0f} {:>5.2f} {:>5.2f} ${:>6,.0f} {:>4}/{:<4} ${:>+5,.0f} ${:>+5,.0f}  {}".format(
        r['name'], r['trades'], r['wr'], r['pnl'], r['pf'], r['sharpe'], r['max_dd'],
        r['long_n'], r['short_n'], r['avg_win'], r['avg_loss'], ex))

# === BEST CONFIG: detail par annee ===
if results:
    best = sorted(results, key=lambda x: x['pnl'], reverse=True)[0]
    print()
    print('=' * 80)
    print('  MEILLEURE CONFIG: {}'.format(best['name']))
    print('=' * 80)
    print()
    for y in sorted(best['yearly'].keys()):
        print("  {} : ${:>+8,.0f}".format(y, best['yearly'][y]))

    # Trade detail
    print()
    print('  DETAIL TRADES:')
    print('  ' + '-' * 90)
    tdf = pd.DataFrame(best['report'].trades)
    for _, t in tdf.iterrows():
        marker = '+' if t['pnl_usd'] > 0 else '!!!'
        ev_short = t['news_event'][:40]
        print("  {} {:>5} {:>5} e={:>8.1f} x={:>8.1f} {:>+7.1f}pts ${:>+6,.0f} {:>5} {}  {}".format(
            t['date'], t['news_time_et'], t['direction'],
            t['entry'], t['exit'], t['pnl_pts'], t['pnl_usd'],
            t['exit_reason'], ev_short, marker))

print()
print('=' * 80)
