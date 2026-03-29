"""
OPTIMISATION FINE — H1 SMA Distance
=====================================
Grid fin entre 20 et 100, detail par annee.
"""
import sys
from pathlib import Path
import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backtester.mm20_engine import MM20BacktestEngine

BASE_DIR = Path(__file__).resolve().parent.parent
PV = 8.0

df = pd.read_csv(BASE_DIR / 'data' / 'databento_nq_5min_5y.csv',
                 index_col=0, parse_dates=True)
print("Data: {} bars, 5 ans\n".format(len(df)))

BEST = dict(
    tp_points=300, trail_bars=20, max_trades_day=4, sma_period=20,
    min_sma_dist=0, max_sma_dist=0, atr_min=0, daily_loss_stop=3,
    point_value=PV, daily_loss_usd=1000, pullback_bars=10, pullback_dist=15,
    max_sl_pts=200, start_offset_min=30, abs_start_hour=0,
)

def run(params):
    engine = MM20BacktestEngine(**params)
    report = engine.run(df)
    if not report or report.total_trades < 50:
        return None
    tdf = pd.DataFrame(report.trades)
    tdf['dp'] = pd.to_datetime(tdf['date'])
    tdf['month'] = tdf['dp'].dt.to_period('M')
    tdf['year'] = tdf['dp'].dt.year
    mp = sum(1 for _, g in tdf.groupby('month') if g['pnl_usd'].sum() > 0)
    mn = sum(1 for _, g in tdf.groupby('month') if g['pnl_usd'].sum() <= 0)
    yearly = {}
    for y, g in tdf.groupby('year'):
        yearly[int(y)] = round(g['pnl_usd'].sum(), 0)
    # direction stats
    long_t = tdf[tdf['direction'] == 'long']
    short_t = tdf[tdf['direction'] == 'short']
    return dict(trades=report.total_trades, wr=report.win_rate, pnl=report.total_pnl_usd,
                pf=report.profit_factor, sharpe=report.sharpe_ratio, max_dd=report.max_drawdown_usd,
                avg_trade=report.avg_trade, avg_win=report.avg_win, avg_loss=report.avg_loss,
                mp=mp, mn=mn, yearly=yearly,
                long_n=len(long_t), long_pnl=long_t['pnl_usd'].sum(),
                long_wr=len(long_t[long_t['pnl_usd']>0])/len(long_t)*100 if len(long_t)>0 else 0,
                short_n=len(short_t), short_pnl=short_t['pnl_usd'].sum(),
                short_wr=len(short_t[short_t['pnl_usd']>0])/len(short_t)*100 if len(short_t)>0 else 0)

# === GRID FIN ===
print('=' * 150)
print('  GRID FIN h1_sma_dist (5 ANS)')
print('=' * 150)
hdr = "  {:<8} {:>5} {:>6} {:>11} {:>5} {:>6} {:>8} {:>5}  {:>9} {:>9} {:>9} {:>9} {:>9} {:>9}  {:>12} {:>12}".format(
    'Dist', 'Tr', 'WR', 'PnL', 'PF', 'Sharp', 'MaxDD', 'M+',
    '2021', '2022', '2023', '2024', '2025', '2026',
    'LONG', 'SHORT')
print(hdr)
print('  ' + '-' * 148)

grid = [0, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70, 75, 80, 85, 90, 95, 100, 120, 150]

for d in grid:
    params = dict(BEST, min_h1_sma_dist=d)
    r = run(params)
    if r:
        tm = r['mp'] + r['mn']
        y = r['yearly']
        long_info = "{}t {:.0f}%".format(r['long_n'], r['long_wr'])
        short_info = "{}t {:.0f}%".format(r['short_n'], r['short_wr'])
        print("  {:<8} {:>5} {:>5.1f}% ${:>+9,.0f} {:>5.2f} {:>5.2f} ${:>7,.0f} {}/{}  ${:>+8,.0f} ${:>+8,.0f} ${:>+8,.0f} ${:>+8,.0f} ${:>+8,.0f} ${:>+8,.0f}  {:>12} {:>12}".format(
            d, r['trades'], r['wr'], r['pnl'], r['pf'], r['sharpe'], r['max_dd'],
            r['mp'], tm,
            y.get(2021, 0), y.get(2022, 0), y.get(2023, 0), y.get(2024, 0), y.get(2025, 0), y.get(2026, 0),
            long_info, short_info))

# === MEILLEUR h1_dist + fine-tuning autres params ===
print()
print('=' * 150)
print('  FINE-TUNING AUTOUR DU MEILLEUR h1_dist')
print('=' * 150)

# Pick h1_dist=75 as sweet spot, now try other params
H1D = 75
combos = [
    ('h1d=75 (base)',                           dict(BEST, min_h1_sma_dist=H1D)),
    ('h1d=75 + tp=200',                         dict(BEST, min_h1_sma_dist=H1D, tp_points=200)),
    ('h1d=75 + tp=250',                         dict(BEST, min_h1_sma_dist=H1D, tp_points=250)),
    ('h1d=75 + tp=400',                         dict(BEST, min_h1_sma_dist=H1D, tp_points=400)),
    ('h1d=75 + tp=500',                         dict(BEST, min_h1_sma_dist=H1D, tp_points=500)),
    ('h1d=75 + trail=15',                        dict(BEST, min_h1_sma_dist=H1D, trail_bars=15)),
    ('h1d=75 + trail=25',                        dict(BEST, min_h1_sma_dist=H1D, trail_bars=25)),
    ('h1d=75 + trail=30',                        dict(BEST, min_h1_sma_dist=H1D, trail_bars=30)),
    ('h1d=75 + mt=3',                            dict(BEST, min_h1_sma_dist=H1D, max_trades_day=3)),
    ('h1d=75 + mt=5',                            dict(BEST, min_h1_sma_dist=H1D, max_trades_day=5)),
    ('h1d=75 + mt=6',                            dict(BEST, min_h1_sma_dist=H1D, max_trades_day=6)),
    ('h1d=75 + dls=2',                            dict(BEST, min_h1_sma_dist=H1D, daily_loss_stop=2)),
    ('h1d=75 + dls=4',                            dict(BEST, min_h1_sma_dist=H1D, daily_loss_stop=4)),
    ('h1d=75 + dlu=750',                          dict(BEST, min_h1_sma_dist=H1D, daily_loss_usd=750)),
    ('h1d=75 + max_sl=150',                       dict(BEST, min_h1_sma_dist=H1D, max_sl_pts=150)),
    ('h1d=75 + max_sl=250',                       dict(BEST, min_h1_sma_dist=H1D, max_sl_pts=250)),
    ('h1d=75 + pb_bars=5',                        dict(BEST, min_h1_sma_dist=H1D, pullback_bars=5)),
    ('h1d=75 + pb_bars=15',                       dict(BEST, min_h1_sma_dist=H1D, pullback_bars=15)),
    ('h1d=75 + pb_dist=5',                        dict(BEST, min_h1_sma_dist=H1D, pullback_dist=5)),
    ('h1d=75 + pb_dist=20',                       dict(BEST, min_h1_sma_dist=H1D, pullback_dist=20)),
    # Combos des meilleurs
    ('h1d=75 + tp=400 + trail=25',                dict(BEST, min_h1_sma_dist=H1D, tp_points=400, trail_bars=25)),
    ('h1d=75 + tp=400 + mt=5',                    dict(BEST, min_h1_sma_dist=H1D, tp_points=400, max_trades_day=5)),
    ('h1d=75 + trail=25 + mt=5',                  dict(BEST, min_h1_sma_dist=H1D, trail_bars=25, max_trades_day=5)),
    ('h1d=75 + tp=400 + trail=25 + mt=5',         dict(BEST, min_h1_sma_dist=H1D, tp_points=400, trail_bars=25, max_trades_day=5)),
    ('h1d=75 + max_sl=250 + trail=25',            dict(BEST, min_h1_sma_dist=H1D, max_sl_pts=250, trail_bars=25)),
    ('h1d=75 + max_sl=250 + tp=400',              dict(BEST, min_h1_sma_dist=H1D, max_sl_pts=250, tp_points=400)),
]

results = []
for name, params in combos:
    r = run(params)
    if r:
        r['label'] = name
        results.append(r)

results.sort(key=lambda x: x['pnl'] * x['pf'] / x['max_dd'] if x['max_dd'] > 0 else 0, reverse=True)

print()
print("  {:<45} {:>5} {:>6} {:>11} {:>5} {:>6} {:>8} {:>5} {:>8} {:>8}".format(
    'Config', 'Tr', 'WR', 'PnL', 'PF', 'Sharp', 'MaxDD', 'M+', 'AvgWin', 'AvgLoss'))
print('  ' + '-' * 115)
for r in results:
    tm = r['mp'] + r['mn']
    print("  {:<45} {:>5} {:>5.1f}% ${:>+9,.0f} {:>5.2f} {:>5.2f} ${:>7,.0f} {}/{} ${:>+6,.0f} ${:>+6,.0f}".format(
        r['label'], r['trades'], r['wr'], r['pnl'], r['pf'], r['sharpe'], r['max_dd'],
        r['mp'], tm, r['avg_win'], r['avg_loss']))

print()
print('=' * 150)
