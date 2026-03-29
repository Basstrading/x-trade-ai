"""Combinaisons finales autour de max_sl=200 sur 5 ans."""
import sys
from pathlib import Path
import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backtester.mm20_engine import MM20BacktestEngine

df = pd.read_csv(Path(__file__).resolve().parent.parent / 'data' / 'databento_nq_5min_5y.csv',
                 index_col=0, parse_dates=True)
PV = 8.0

REF = dict(
    tp_points=300, trail_bars=15, max_trades_day=4, sma_period=20,
    start_offset_min=30, min_sma_dist=0, atr_min=0, daily_loss_stop=2,
    point_value=PV, daily_loss_usd=1000, pullback_bars=10, pullback_dist=15,
)

def run(params):
    engine = MM20BacktestEngine(**params)
    report = engine.run(df)
    if not report or report.total_trades < 100:
        return None
    tdf = pd.DataFrame(report.trades)
    tdf['dp'] = pd.to_datetime(tdf['date'])
    tdf['month'] = tdf['dp'].dt.to_period('M')
    mp = sum(1 for _, g in tdf.groupby('month') if g['pnl_usd'].sum() > 0)
    mn = sum(1 for _, g in tdf.groupby('month') if g['pnl_usd'].sum() <= 0)
    tdf['year'] = tdf['dp'].dt.year
    yearly = {}
    for y, g in tdf.groupby('year'):
        yearly[int(y)] = round(g['pnl_usd'].sum(), 0)
    return dict(trades=report.total_trades, wr=report.win_rate, pnl=report.total_pnl_usd,
                pf=report.profit_factor, sharpe=report.sharpe_ratio, max_dd=report.max_drawdown_usd,
                avg_trade=report.avg_trade, mp=mp, mn=mn, yearly=yearly)

combos = [
    ('REFERENCE',                        REF),
    ('max_sl=200',                       dict(REF, max_sl_pts=200)),
    ('max_sl=175',                       dict(REF, max_sl_pts=175)),
    ('max_sl=225',                       dict(REF, max_sl_pts=225)),
    ('max_sl=250',                       dict(REF, max_sl_pts=250)),
    ('max_sl=200+tp=250',                dict(REF, max_sl_pts=200, tp_points=250)),
    ('max_sl=200+trail20',               dict(REF, max_sl_pts=200, trail_bars=20)),
    ('max_sl=200+dls=3',                 dict(REF, max_sl_pts=200, daily_loss_stop=3)),
    ('max_sl=200+tp=250+dls=3',          dict(REF, max_sl_pts=200, tp_points=250, daily_loss_stop=3)),
    ('max_sl=200+trail20+dls=3',         dict(REF, max_sl_pts=200, trail_bars=20, daily_loss_stop=3)),
    ('max_sl=200+trail20+tp=250',        dict(REF, max_sl_pts=200, trail_bars=20, tp_points=250)),
    ('max_sl=200+trail20+tp250+dls3',    dict(REF, max_sl_pts=200, trail_bars=20, tp_points=250, daily_loss_stop=3)),
    ('max_sl=200+max_trades=3',          dict(REF, max_sl_pts=200, max_trades_day=3)),
    ('max_sl=200+trail18',               dict(REF, max_sl_pts=200, trail_bars=18)),
    ('max_sl=200+trail18+tp250',         dict(REF, max_sl_pts=200, trail_bars=18, tp_points=250)),
    ('max_sl=200+trail18+dls3',          dict(REF, max_sl_pts=200, trail_bars=18, daily_loss_stop=3)),
    ('max_sl=200+trail18+tp250+dls3',    dict(REF, max_sl_pts=200, trail_bars=18, tp_points=250, daily_loss_stop=3)),
]

print('COMBINAISONS FINALES sur 5 ANS (mars 2021 - mars 2026)')
print('=' * 140)
hdr = "  {:<40} {:>6} {:>6} {:>11} {:>5} {:>6} {:>9} {:>5} {:>9} {:>9} {:>9} {:>9} {:>9}".format(
    'Config', 'Trades', 'WR', 'PnL', 'PF', 'Sharp', 'MaxDD', 'M+', '2021', '2022', '2023', '2024', '2025')
print(hdr)
print('  ' + '-' * 136)

for name, params in combos:
    r = run(params)
    if r:
        tm = r['mp'] + r['mn']
        pct_str = "{}/{}".format(r['mp'], tm)
        y = r['yearly']
        line = "  {:<40} {:>6} {:>5.1f}% ${:>+9,.0f} {:>5.2f} {:>5.2f} ${:>8,.0f} {:>5}".format(
            name, r['trades'], r['wr'], r['pnl'], r['pf'], r['sharpe'], r['max_dd'], pct_str)
        for yr in [2021, 2022, 2023, 2024, 2025]:
            line += " ${:>+8,.0f}".format(y.get(yr, 0))
        print(line)

print('=' * 140)
