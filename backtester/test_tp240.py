"""Test TP=240 vs TP=300 avec h1_dist=75 sur 5 ANS."""
import sys
from pathlib import Path
import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backtester.mm20_engine import MM20BacktestEngine

df = pd.read_csv(Path(__file__).resolve().parent.parent / 'data' / 'databento_nq_5min_5y.csv',
                 index_col=0, parse_dates=True)
PV = 8.0

BEST = dict(
    tp_points=300, trail_bars=20, max_trades_day=4, sma_period=20,
    min_sma_dist=0, max_sma_dist=0, atr_min=0, daily_loss_stop=3,
    point_value=PV, daily_loss_usd=1000, pullback_bars=10, pullback_dist=15,
    max_sl_pts=200, start_offset_min=30, abs_start_hour=0,
    min_h1_sma_dist=75,
)

def run(params):
    engine = MM20BacktestEngine(**params)
    report = engine.run(df)
    if not report:
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
    return dict(trades=report.total_trades, wr=report.win_rate, pnl=report.total_pnl_usd,
                pf=report.profit_factor, sharpe=report.sharpe_ratio, max_dd=report.max_drawdown_usd,
                avg_trade=report.avg_trade, avg_win=report.avg_win, avg_loss=report.avg_loss,
                mp=mp, mn=mn, yearly=yearly)

# Test range autour de 240
tps = [200, 220, 230, 240, 250, 260, 280, 300, 350, 400]

print("h1_dist=75 — Test TP sur 5 ANS")
print("=" * 140)
hdr = "  {:<8} {:>5} {:>6} {:>11} {:>5} {:>6} {:>8} {:>5}  {:>9} {:>9} {:>9} {:>9} {:>9} {:>9}  {:>7} {:>7}".format(
    'TP', 'Tr', 'WR', 'PnL', 'PF', 'Sharp', 'MaxDD', 'M+',
    '2021', '2022', '2023', '2024', '2025', '2026', 'AvgWin', 'AvgLoss')
print(hdr)
print("  " + "-" * 138)

for tp in tps:
    r = run(dict(BEST, tp_points=tp))
    if r:
        tm = r['mp'] + r['mn']
        y = r['yearly']
        print("  {:<8} {:>5} {:>5.1f}% ${:>+9,.0f} {:>5.2f} {:>5.2f} ${:>7,.0f} {}/{}  ${:>+8,.0f} ${:>+8,.0f} ${:>+8,.0f} ${:>+8,.0f} ${:>+8,.0f} ${:>+8,.0f}  ${:>+6,.0f} ${:>+6,.0f}".format(
            tp, r['trades'], r['wr'], r['pnl'], r['pf'], r['sharpe'], r['max_dd'],
            r['mp'], tm,
            y.get(2021,0), y.get(2022,0), y.get(2023,0), y.get(2024,0), y.get(2025,0), y.get(2026,0),
            r['avg_win'], r['avg_loss']))

print("=" * 140)
