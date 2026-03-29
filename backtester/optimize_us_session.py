"""
OPTIMISATION SESSION US — Tester 13h30-14h30-15h30-16h00 Paris
===============================================================
Base: max_sl=200 + trail20 + dls=3 (meilleure config 5 ans)
Validation sur 5 ANS complets.
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
    max_sl_pts=200, start_offset_min=0,
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
    return dict(trades=report.total_trades, wr=report.win_rate, pnl=report.total_pnl_usd,
                pf=report.profit_factor, sharpe=report.sharpe_ratio, max_dd=report.max_drawdown_usd,
                avg_trade=report.avg_trade, avg_win=report.avg_win, avg_loss=report.avg_loss,
                mp=mp, mn=mn, yearly=yearly)

def show(name, r):
    if not r:
        print("  {:<55} NO TRADES".format(name))
        return
    tm = r['mp'] + r['mn']
    print("  {:<55} {:>5} tr | WR {:>5.1f}% | ${:>+9,.0f} | PF {:>5.2f} | Sh {:>5.2f} | DD ${:>7,.0f} | {}/{}M | AvgW ${:>+5,.0f} AvgL ${:>+5,.0f}".format(
        name, r['trades'], r['wr'], r['pnl'], r['pf'], r['sharpe'], r['max_dd'],
        r['mp'], tm, r['avg_win'], r['avg_loss']))

# === ROUND 1: Heure de depart seule ===
print('=' * 140)
print('  ROUND 1: HEURE DE DEPART (5 ANS)')
print('=' * 140)

starts = [
    ('13h00 Paris', 13, 0),
    ('13h30 Paris', 13, 30),
    ('14h00 Paris', 14, 0),
    ('14h30 Paris', 14, 30),
    ('15h00 Paris', 15, 0),
    ('15h30 Paris', 15, 30),
    ('16h00 Paris (ref)', 16, 0),
]

for name, h, m in starts:
    params = dict(BEST, abs_start_hour=h, abs_start_min=m)
    show(name, run(params))

# === ROUND 2: Meilleures heures + max_trades ===
print()
print('=' * 140)
print('  ROUND 2: HEURE DE DEPART + MAX TRADES (5 ANS)')
print('=' * 140)

for h, m, label in [(13, 30, '13h30'), (14, 0, '14h'), (14, 30, '14h30'), (15, 30, '15h30'), (16, 0, '16h')]:
    for mt in [3, 4, 5, 6]:
        params = dict(BEST, abs_start_hour=h, abs_start_min=m, max_trades_day=mt)
        show('{} + max_tr={}'.format(label, mt), run(params))
    print()

# === ROUND 3: Best start + TP ===
print('=' * 140)
print('  ROUND 3: BEST START + TP VARIATIONS (5 ANS)')
print('=' * 140)

for h, m, label in [(14, 0, '14h'), (14, 30, '14h30'), (15, 30, '15h30')]:
    for tp in [200, 250, 300, 400]:
        params = dict(BEST, abs_start_hour=h, abs_start_min=m, tp_points=tp)
        show('{} + tp={}'.format(label, tp), run(params))
    print()

# === ROUND 4: Combinaisons finales ===
print('=' * 140)
print('  ROUND 4: COMBINAISONS FINALES (5 ANS)')
print('=' * 140)

finals = [
    ('16h ref (baseline)',                    dict(BEST, abs_start_hour=16, abs_start_min=0)),
    ('14h + mt=5',                            dict(BEST, abs_start_hour=14, abs_start_min=0, max_trades_day=5)),
    ('14h + mt=5 + tp=250',                   dict(BEST, abs_start_hour=14, abs_start_min=0, max_trades_day=5, tp_points=250)),
    ('14h + mt=5 + tp=400',                   dict(BEST, abs_start_hour=14, abs_start_min=0, max_trades_day=5, tp_points=400)),
    ('14h + mt=4 + tp=250',                   dict(BEST, abs_start_hour=14, abs_start_min=0, max_trades_day=4, tp_points=250)),
    ('14h30 + mt=5',                          dict(BEST, abs_start_hour=14, abs_start_min=30, max_trades_day=5)),
    ('14h30 + mt=5 + tp=250',                 dict(BEST, abs_start_hour=14, abs_start_min=30, max_trades_day=5, tp_points=250)),
    ('14h30 + mt=5 + tp=400',                 dict(BEST, abs_start_hour=14, abs_start_min=30, max_trades_day=5, tp_points=400)),
    ('14h30 + mt=4 + tp=250',                 dict(BEST, abs_start_hour=14, abs_start_min=30, max_trades_day=4, tp_points=250)),
    ('13h30 + mt=5',                          dict(BEST, abs_start_hour=13, abs_start_min=30, max_trades_day=5)),
    ('13h30 + mt=5 + tp=400',                 dict(BEST, abs_start_hour=13, abs_start_min=30, max_trades_day=5, tp_points=400)),
    ('15h30 + mt=5',                          dict(BEST, abs_start_hour=15, abs_start_min=30, max_trades_day=5)),
    ('15h30 + mt=5 + tp=250',                 dict(BEST, abs_start_hour=15, abs_start_min=30, max_trades_day=5, tp_points=250)),
    ('14h + mt=5 + trail=25',                 dict(BEST, abs_start_hour=14, abs_start_min=0, max_trades_day=5, trail_bars=25)),
    ('14h30 + mt=5 + trail=25',               dict(BEST, abs_start_hour=14, abs_start_min=30, max_trades_day=5, trail_bars=25)),
    ('14h + mt=5 + min_sma=10',               dict(BEST, abs_start_hour=14, abs_start_min=0, max_trades_day=5, min_sma_dist=10)),
    ('14h30 + mt=5 + min_sma=10',             dict(BEST, abs_start_hour=14, abs_start_min=30, max_trades_day=5, min_sma_dist=10)),
]

results = []
for name, params in finals:
    r = run(params)
    if r:
        r['label'] = name
        results.append(r)
        show(name, r)

# Sort by PnL * PF / DD
print()
print('=' * 140)
print('  CLASSEMENT PAR SCORE (PnL * PF / MaxDD)')
print('=' * 140)
for r in results:
    r['score'] = r['pnl'] * r['pf'] / r['max_dd'] if r['max_dd'] > 0 else 0

results.sort(key=lambda x: x['score'], reverse=True)
print("  {:<55} {:>5} {:>6} {:>11} {:>5} {:>6} {:>9} {:>5} {:>8}".format(
    'Config', 'Tr', 'WR', 'PnL', 'PF', 'Sharp', 'MaxDD', 'M+', 'Score'))
print('  ' + '-' * 115)
for r in results:
    tm = r['mp'] + r['mn']
    print("  {:<55} {:>5} {:>5.1f}% ${:>+9,.0f} {:>5.2f} {:>5.2f} ${:>7,.0f} {}/{} {:>8,.0f}".format(
        r['label'], r['trades'], r['wr'], r['pnl'], r['pf'], r['sharpe'], r['max_dd'],
        r['mp'], tm, r['score']))
print('=' * 140)
