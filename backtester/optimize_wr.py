"""
OPTIMISATION WR — Partir de 8h00 Paris, ameliorer le win rate
==============================================================
Base: max_sl=200, trail20, dls=3, 8h00 Paris
WR actuel: 40.3% — objectif: >45% sans trop perdre de PnL

Axes testes:
1. Filtrer les heures toxiques (7h=29.7% WR, 11h=18.9%)
2. Min distance SMA M5 (eviter les faux signaux pres de la SMA)
3. Max distance SMA M5 (eviter les entrees sur-etendues)
4. ATR minimum (eviter les marches morts)
5. Pullback params (resserrer)
6. Combinaisons des meilleures ameliorations
"""
import sys
from pathlib import Path
import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backtester.mm20_engine import MM20BacktestEngine

BASE_DIR = Path(__file__).resolve().parent.parent
PV = 8.0

df_full = pd.read_csv(BASE_DIR / 'data' / 'databento_nq_5min_5y.csv',
                      index_col=0, parse_dates=True)
df = df_full[df_full.index >= '2025-09-10'].copy()
print("Data: {} bars, {} -> {} (6 mois)".format(len(df), df.index.min().date(), df.index.max().date()))

# Nouvelle baseline: 8h00 Paris
BASE = dict(
    tp_points=300, trail_bars=20, max_trades_day=4, sma_period=20,
    min_sma_dist=0, max_sma_dist=0, atr_min=0, daily_loss_stop=3,
    point_value=PV, daily_loss_usd=1000, pullback_bars=10, pullback_dist=15,
    max_sl_pts=200, start_offset_min=0, abs_start_hour=8, abs_start_min=0,
)

def run(params):
    engine = MM20BacktestEngine(**params)
    report = engine.run(df)
    if not report or report.total_trades < 10:
        return None
    tdf = pd.DataFrame(report.trades)
    tdf['dp'] = pd.to_datetime(tdf['date'])
    tdf['month'] = tdf['dp'].dt.to_period('M')
    mp = sum(1 for _, g in tdf.groupby('month') if g['pnl_usd'].sum() > 0)
    mn = sum(1 for _, g in tdf.groupby('month') if g['pnl_usd'].sum() <= 0)
    return dict(trades=report.total_trades, wr=report.win_rate, pnl=report.total_pnl_usd,
                pf=report.profit_factor, sharpe=report.sharpe_ratio, max_dd=report.max_drawdown_usd,
                avg_trade=report.avg_trade, mp=mp, mn=mn)

def show(name, r):
    if not r:
        print("  {:<50} AUCUN TRADE".format(name))
        return
    tm = r['mp'] + r['mn']
    print("  {:<50} {:>5} tr | WR {:>5.1f}% | PnL ${:>+9,.0f} | PF {:>5.2f} | Sh {:>5.2f} | DD ${:>7,.0f} | {}/{}M".format(
        name, r['trades'], r['wr'], r['pnl'], r['pf'], r['sharpe'], r['max_dd'], r['mp'], tm))

# === REFERENCE ===
print()
ref = run(BASE)
show('BASE 8h00', ref)
print()

# === TEST 1: Heures de trading ===
print('--- Test 1: Heures de debut ---')
for h in [8, 9, 10]:
    for m in [0, 30]:
        params = dict(BASE, abs_start_hour=h, abs_start_min=m)
        show('start={:02d}h{:02d}'.format(h, m), run(params))

# === TEST 2: Min distance SMA M5 ===
print()
print('--- Test 2: Min distance SMA M5 ---')
for d in [5, 10, 15, 20, 25, 30, 40]:
    params = dict(BASE, min_sma_dist=d)
    show('min_sma_dist={}'.format(d), run(params))

# === TEST 3: Max distance SMA M5 ===
print()
print('--- Test 3: Max distance SMA M5 ---')
for d in [50, 75, 100, 150, 200, 300]:
    params = dict(BASE, max_sma_dist=d)
    show('max_sma_dist={}'.format(d), run(params))

# === TEST 4: ATR minimum ===
print()
print('--- Test 4: ATR minimum ---')
for a in [5, 8, 10, 12, 15, 20, 25]:
    params = dict(BASE, atr_min=a)
    show('atr_min={}'.format(a), run(params))

# === TEST 5: Pullback bars ===
print()
print('--- Test 5: Pullback bars ---')
for pb in [3, 5, 7, 8, 12, 15]:
    params = dict(BASE, pullback_bars=pb)
    show('pb_bars={}'.format(pb), run(params))

# === TEST 6: Pullback distance ===
print()
print('--- Test 6: Pullback distance ---')
for pd_val in [5, 8, 10, 12, 20, 25]:
    params = dict(BASE, pullback_dist=pd_val)
    show('pb_dist={}'.format(pd_val), run(params))

# === TEST 7: TP points ===
print()
print('--- Test 7: TP points ---')
for tp in [150, 200, 250, 400]:
    params = dict(BASE, tp_points=tp)
    show('tp={}'.format(tp), run(params))

# === TEST 8: Trail bars ===
print()
print('--- Test 8: Trail bars ---')
for tb in [15, 18, 22, 25, 30]:
    params = dict(BASE, trail_bars=tb)
    show('trail={}'.format(tb), run(params))

# === TEST 9: Max trades/jour ===
print()
print('--- Test 9: Max trades/jour ---')
for mt in [2, 3, 5, 6, 8]:
    params = dict(BASE, max_trades_day=mt)
    show('max_tr={}'.format(mt), run(params))

# === TEST 10: Max SL ===
print()
print('--- Test 10: Max SL ---')
for sl in [100, 150, 175, 225, 250, 300]:
    params = dict(BASE, max_sl_pts=sl)
    show('max_sl={}'.format(sl), run(params))

# === TEST 11: Daily loss USD ===
print()
print('--- Test 11: Daily loss USD ---')
for dlu in [500, 750, 1500, 2000]:
    params = dict(BASE, daily_loss_usd=dlu)
    show('dlu={}'.format(dlu), run(params))

# === COMBINAISONS ===
print()
print('=' * 120)
print('  COMBINAISONS DES MEILLEURS FILTRES')
print('=' * 120)

combos = [
    ('BASE 8h00',                                BASE),
    ('min_sma=15',                               dict(BASE, min_sma_dist=15)),
    ('min_sma=20',                               dict(BASE, min_sma_dist=20)),
    ('min_sma=20 + atr=10',                      dict(BASE, min_sma_dist=20, atr_min=10)),
    ('min_sma=20 + atr=12',                      dict(BASE, min_sma_dist=20, atr_min=12)),
    ('min_sma=20 + atr=15',                      dict(BASE, min_sma_dist=20, atr_min=15)),
    ('min_sma=20 + max_sma=150',                 dict(BASE, min_sma_dist=20, max_sma_dist=150)),
    ('min_sma=20 + pb_bars=7',                   dict(BASE, min_sma_dist=20, pullback_bars=7)),
    ('min_sma=20 + pb_dist=10',                  dict(BASE, min_sma_dist=20, pullback_dist=10)),
    ('min_sma=20 + atr=10 + pb_bars=7',          dict(BASE, min_sma_dist=20, atr_min=10, pullback_bars=7)),
    ('min_sma=20 + atr=10 + max_sma=150',        dict(BASE, min_sma_dist=20, atr_min=10, max_sma_dist=150)),
    ('min_sma=20 + atr=10 + trail=25',           dict(BASE, min_sma_dist=20, atr_min=10, trail_bars=25)),
    ('min_sma=15 + atr=10',                      dict(BASE, min_sma_dist=15, atr_min=10)),
    ('min_sma=15 + atr=10 + max_sma=200',        dict(BASE, min_sma_dist=15, atr_min=10, max_sma_dist=200)),
    ('min_sma=25 + atr=10',                      dict(BASE, min_sma_dist=25, atr_min=10)),
    ('start=9h + min_sma=20 + atr=10',           dict(BASE, abs_start_hour=9, min_sma_dist=20, atr_min=10)),
    ('start=10h + min_sma=20',                   dict(BASE, abs_start_hour=10, min_sma_dist=20)),
]

combo_results = []
for name, params in combos:
    r = run(params)
    if r:
        r['label'] = name
        combo_results.append(r)

combo_results.sort(key=lambda x: x['pnl'], reverse=True)

print()
print("  {:<50} {:>5} {:>6} {:>11} {:>5} {:>6} {:>9} {:>5}".format(
    'Config', 'Tr', 'WR', 'PnL', 'PF', 'Sharp', 'MaxDD', 'M+'))
print('  ' + '-' * 100)
for r in combo_results:
    tm = r['mp'] + r['mn']
    print("  {:<50} {:>5} {:>5.1f}% ${:>+9,.0f} {:>5.2f} {:>5.2f} ${:>7,.0f} {}/{}".format(
        r['label'], r['trades'], r['wr'], r['pnl'], r['pf'], r['sharpe'], r['max_dd'], r['mp'], tm))

print('=' * 120)
