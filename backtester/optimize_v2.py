"""
OPTIMISATION V2 — MM20 Pullback
================================
Axes d'amelioration identifies par l'analyse des trades:
1. Trail bars short asymetrique (short perd plus, trail trop large?)
2. Max trades/jour (trade #4 est catastrophique, #3 est bon)
3. TP dynamique (seulement 2% atteignent 300pts, peut-on faire mieux?)
4. Stop loss max (cap les gros perdants > -$500 = -$178K de pertes)
5. Pullback distance (affiner)
6. Trail bars long (tester 10-20)
"""
import sys
from pathlib import Path
import pandas as pd
import numpy as np
import time
import itertools

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backtester.mm20_engine import MM20BacktestEngine

BASE_DIR = Path(__file__).resolve().parent.parent
POINT_VALUE = 8.0

df = pd.read_csv(BASE_DIR / 'data' / 'databento_nq_5min_5y.csv',
                 index_col=0, parse_dates=True)
print(f"Data: {len(df)} bars, 5 ans\n")

# Reference baseline
REF = dict(
    tp_points=300, trail_bars=15, max_trades_day=4, sma_period=20,
    start_offset_min=30, min_sma_dist=0, atr_min=0, daily_loss_stop=2,
    point_value=POINT_VALUE, daily_loss_usd=1000, pullback_bars=10, pullback_dist=15,
)

def run_and_score(params, label=""):
    engine = MM20BacktestEngine(**params)
    report = engine.run(df)
    if not report or report.total_trades < 100:
        return None
    tdf = pd.DataFrame(report.trades)
    tdf['dp'] = pd.to_datetime(tdf['date'])
    tdf['month'] = tdf['dp'].dt.to_period('M')
    mp = sum(1 for _, g in tdf.groupby('month') if g['pnl_usd'].sum() > 0)
    mn = sum(1 for _, g in tdf.groupby('month') if g['pnl_usd'].sum() <= 0)
    return {
        'label': label,
        'trades': report.total_trades,
        'wr': report.win_rate,
        'pnl': report.total_pnl_usd,
        'pf': report.profit_factor,
        'sharpe': report.sharpe_ratio,
        'max_dd': report.max_drawdown_usd,
        'avg_trade': report.avg_trade,
        'mp': mp, 'mn': mn,
    }

results = []

# === REFERENCE ===
t0 = time.time()
ref = run_and_score(REF, "REFERENCE (baseline)")
results.append(ref)
print(f"REF: PF {ref['pf']:.2f} Sharpe {ref['sharpe']:.2f} PnL ${ref['pnl']:+,.0f} ({time.time()-t0:.1f}s)")

# === TEST 1: Trail bars short asymetrique ===
print("\n--- Test 1: Trail bars SHORT ---")
for tb_short in [5, 7, 9, 10, 12]:
    params = dict(REF, trail_bars_short=tb_short)
    r = run_and_score(params, f"trail_short={tb_short}")
    if r:
        results.append(r)
        print(f"  trail_short={tb_short:>2}: PF {r['pf']:.2f} Sharpe {r['sharpe']:.2f} PnL ${r['pnl']:+,.0f} | {r['trades']} trades")

# === TEST 2: Trail bars long ===
print("\n--- Test 2: Trail bars LONG ---")
for tb_long in [8, 10, 12, 18, 20, 25]:
    params = dict(REF, trail_bars=tb_long)
    r = run_and_score(params, f"trail_long={tb_long}")
    if r:
        results.append(r)
        print(f"  trail_long={tb_long:>2}: PF {r['pf']:.2f} Sharpe {r['sharpe']:.2f} PnL ${r['pnl']:+,.0f} | {r['trades']} trades")

# === TEST 3: Max trades/jour ===
print("\n--- Test 3: Max trades/jour ---")
for mt in [1, 2, 3, 5, 6]:
    params = dict(REF, max_trades_day=mt)
    r = run_and_score(params, f"max_trades={mt}")
    if r:
        results.append(r)
        print(f"  max_trades={mt}: PF {r['pf']:.2f} Sharpe {r['sharpe']:.2f} PnL ${r['pnl']:+,.0f} | {r['trades']} trades")

# === TEST 4: TP points ===
print("\n--- Test 4: Take Profit ---")
for tp in [100, 150, 200, 250, 400, 500]:
    params = dict(REF, tp_points=tp)
    r = run_and_score(params, f"tp={tp}")
    if r:
        results.append(r)
        print(f"  tp={tp:>3}: PF {r['pf']:.2f} Sharpe {r['sharpe']:.2f} PnL ${r['pnl']:+,.0f} | {r['trades']} trades")

# === TEST 5: Max Stop Loss fixe (cap les gros perdants) ===
print("\n--- Test 5: Max Stop Loss fixe ---")
for sl in [50, 75, 100, 125, 150, 200]:
    params = dict(REF, max_sl_pts=sl)
    r = run_and_score(params, f"max_sl={sl}")
    if r:
        results.append(r)
        print(f"  max_sl={sl:>3}: PF {r['pf']:.2f} Sharpe {r['sharpe']:.2f} PnL ${r['pnl']:+,.0f} | {r['trades']} trades | DD ${r['max_dd']:,.0f}")

# === TEST 6: Daily loss stop (nb pertes consecutives) ===
print("\n--- Test 6: Daily loss stop ---")
for dls in [1, 3, 4, 5]:
    params = dict(REF, daily_loss_stop=dls)
    r = run_and_score(params, f"daily_loss_stop={dls}")
    if r:
        results.append(r)
        print(f"  daily_loss_stop={dls}: PF {r['pf']:.2f} Sharpe {r['sharpe']:.2f} PnL ${r['pnl']:+,.0f} | {r['trades']} trades")

# === TEST 7: Daily loss USD ===
print("\n--- Test 7: Daily loss USD ---")
for dlu in [500, 750, 1500, 2000, 0]:
    params = dict(REF, daily_loss_usd=dlu)
    r = run_and_score(params, f"daily_loss_usd={dlu}")
    if r:
        results.append(r)
        print(f"  daily_loss_usd={dlu:>4}: PF {r['pf']:.2f} Sharpe {r['sharpe']:.2f} PnL ${r['pnl']:+,.0f} | {r['trades']} trades")

# === TEST 8: Pullback bars ===
print("\n--- Test 8: Pullback bars ---")
for pb in [5, 8, 12, 15, 20]:
    params = dict(REF, pullback_bars=pb)
    r = run_and_score(params, f"pb_bars={pb}")
    if r:
        results.append(r)
        print(f"  pb_bars={pb:>2}: PF {r['pf']:.2f} Sharpe {r['sharpe']:.2f} PnL ${r['pnl']:+,.0f} | {r['trades']} trades")

# === TEST 9: Pullback distance ===
print("\n--- Test 9: Pullback distance ---")
for pd_val in [5, 10, 20, 25, 30]:
    params = dict(REF, pullback_dist=pd_val)
    r = run_and_score(params, f"pb_dist={pd_val}")
    if r:
        results.append(r)
        print(f"  pb_dist={pd_val:>2}: PF {r['pf']:.2f} Sharpe {r['sharpe']:.2f} PnL ${r['pnl']:+,.0f} | {r['trades']} trades")

# === TEST 10: Start offset ===
print("\n--- Test 10: Start offset ---")
for so in [0, 15, 45, 60, 90]:
    params = dict(REF, start_offset_min=so)
    r = run_and_score(params, f"start_offset={so}")
    if r:
        results.append(r)
        print(f"  start_offset={so:>2}min: PF {r['pf']:.2f} Sharpe {r['sharpe']:.2f} PnL ${r['pnl']:+,.0f} | {r['trades']} trades")

# === BEST COMBOS ===
# From individual tests, pick the best improvements and combine
print("\n\n" + "=" * 100)
print("  COMBINAISONS DES MEILLEURES AMELIORATIONS")
print("=" * 100)

combos = {
    'REF': REF,
    'trail_short=7': dict(REF, trail_bars_short=7),
    'max_sl=100': dict(REF, max_sl_pts=100),
    'trail_short=7 + max_sl=100': dict(REF, trail_bars_short=7, max_sl_pts=100),
    'trail_short=7 + max_sl=75': dict(REF, trail_bars_short=7, max_sl_pts=75),
    'trail_short=9 + max_sl=100': dict(REF, trail_bars_short=9, max_sl_pts=100),
    'trail_short=7 + max_sl=100 + tp=200': dict(REF, trail_bars_short=7, max_sl_pts=100, tp_points=200),
    'trail_short=7 + max_sl=100 + max_trades=3': dict(REF, trail_bars_short=7, max_sl_pts=100, max_trades_day=3),
    'trail_short=10 + max_sl=125': dict(REF, trail_bars_short=10, max_sl_pts=125),
    'trail_short=7 + max_sl=100 + trail_long=12': dict(REF, trail_bars_short=7, max_sl_pts=100, trail_bars=12),
    'trail_short=7 + max_sl=100 + pb_bars=8': dict(REF, trail_bars_short=7, max_sl_pts=100, pullback_bars=8),
}

combo_results = []
for name, params in combos.items():
    r = run_and_score(params, name)
    if r:
        combo_results.append(r)

combo_results.sort(key=lambda x: x['sharpe'], reverse=True)

print(f"\n  {'Config':<45} {'Trades':>7} {'WR':>6} {'PnL':>12} {'PF':>6} {'Sharpe':>7} {'MaxDD':>10} {'Mois+':>6}")
print(f"  {'-' * 105}")
for r in combo_results:
    tm = r['mp'] + r['mn']
    pct = r['mp'] / tm * 100 if tm > 0 else 0
    print(f"  {r['label']:<45} {r['trades']:>7} {r['wr']:>5.1f}% ${r['pnl']:>+10,.0f} {r['pf']:>5.2f} {r['sharpe']:>6.2f} ${r['max_dd']:>9,.0f} {pct:>5.0f}%")

print("\n" + "=" * 100)
