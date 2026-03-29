"""
TEST STOPS COURTS SUR SHORTS — MM20 Pullback
=============================================
Teste differentes combinaisons trail_bars_short / trail_delta_short
vs la config de reference (15 bars, 0 delta pour les shorts)
"""
import sys
from pathlib import Path
import pandas as pd
import numpy as np
import itertools
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backtester.mm20_engine import MM20BacktestEngine

BASE_DIR = Path(__file__).resolve().parent.parent
POINT_VALUE = 8.0

df = pd.read_csv(BASE_DIR / 'data' / 'databento_nq_5min.csv', index_col='datetime', parse_dates=True)
print(f"Databento NQ 5min: {len(df)} barres\n")

# Config de base pullback
BASE = dict(
    tp_points=300,
    trail_bars=15,          # longs : 15 bars (inchange)
    max_trades_day=4,
    sma_period=20,
    start_offset_min=30,
    min_sma_dist=0,
    atr_min=0,
    daily_loss_stop=2,
    point_value=POINT_VALUE,
    daily_loss_usd=1000,
    pullback_bars=10,
    pullback_dist=15,
)

# Grille pour les shorts
trail_bars_short_grid = [3, 5, 7, 9, 12, 15]
trail_delta_short_grid = [0, 10, 20, 30, 46, 60]

results = []
total = len(trail_bars_short_grid) * len(trail_delta_short_grid)
print(f"Test {total} combinaisons short stop...")
t0 = time.time()

for idx, (tb_s, td_s) in enumerate(itertools.product(trail_bars_short_grid, trail_delta_short_grid)):
    engine = MM20BacktestEngine(
        **BASE,
        trail_bars_short=tb_s,
        trail_delta_short=td_s,
    )
    report = engine.run(df)

    if report and report.total_trades >= 50:
        eq = np.array(report.equity_curve)
        peak = np.maximum.accumulate(eq)
        max_dd = abs((eq - peak).min())

        # Separer stats longs vs shorts
        longs = [t for t in report.trades if t['direction'] == 'long']
        shorts = [t for t in report.trades if t['direction'] == 'short']

        l_pnls = [t['pnl_usd'] for t in longs]
        s_pnls = [t['pnl_usd'] for t in shorts]

        l_wr = len([p for p in l_pnls if p > 0]) / len(l_pnls) * 100 if l_pnls else 0
        s_wr = len([p for p in s_pnls if p > 0]) / len(s_pnls) * 100 if s_pnls else 0

        l_pnl = sum(l_pnls)
        s_pnl = sum(s_pnls)

        l_avg = np.mean(l_pnls) if l_pnls else 0
        s_avg = np.mean(s_pnls) if s_pnls else 0

        pf = report.profit_factor
        score = (report.total_pnl_usd * pf / max_dd) if max_dd > 0 else 0

        results.append({
            'tb_s': tb_s,
            'td_s': td_s,
            'trades': report.total_trades,
            'n_long': len(longs),
            'n_short': len(shorts),
            'wr': report.win_rate,
            'l_wr': round(l_wr, 1),
            's_wr': round(s_wr, 1),
            'pnl': report.total_pnl_usd,
            'l_pnl': round(l_pnl, 0),
            's_pnl': round(s_pnl, 0),
            'pf': pf,
            'sharpe': report.sharpe_ratio,
            'max_dd': max_dd,
            'avg_trade': report.avg_trade,
            'l_avg': round(l_avg, 0),
            's_avg': round(s_avg, 0),
            'score': score,
        })

    if (idx + 1) % 12 == 0:
        print(f"  {idx+1}/{total} ({time.time()-t0:.0f}s)")

print(f"Termine en {time.time()-t0:.0f}s\n")

results.sort(key=lambda x: x['score'], reverse=True)

# Reference = 15 bars, 0 delta (meme que les longs)
ref = next((r for r in results if r['tb_s'] == 15 and r['td_s'] == 0), None)

# Afficher
print("=" * 130)
print("  TOP 15 CONFIGURATIONS SHORT STOP  (Longs: 15 bars inchanges)")
print("=" * 130)
print(f"  {'#':>2}  {'Bars':>5}  {'Delta':>5}  {'Trades':>6}  {'L/S':>7}  {'WR':>5}  {'L WR':>5}  {'S WR':>5}  {'PnL Total':>11}  {'L PnL':>9}  {'S PnL':>9}  {'PF':>5}  {'Sharpe':>6}  {'MaxDD':>9}  {'Score':>7}")
print("-" * 130)

for i, r in enumerate(results[:15]):
    marker = " <-- REF" if r['tb_s'] == 15 and r['td_s'] == 0 else ""
    print(f"  {i+1:>2}  {r['tb_s']:>5}  {r['td_s']:>5}  {r['trades']:>6}  {r['n_long']}/{r['n_short']:<4}  {r['wr']:>4.1f}%  {r['l_wr']:>4.1f}%  {r['s_wr']:>4.1f}%  ${r['pnl']:>+9,.0f}  ${r['l_pnl']:>+8,.0f}  ${r['s_pnl']:>+8,.0f}  {r['pf']:>4.2f}  {r['sharpe']:>5.2f}  ${r['max_dd']:>8,.0f}  {r['score']:>7,.0f}{marker}")

# Comparaison directe : ref vs best
print("\n" + "=" * 80)
print("  COMPARAISON : REFERENCE (15/0) vs BEST SHORT STOP")
print("=" * 80)

best = results[0]

def cmp(label, v_ref, v_best, fmt='$'):
    if fmt == '$':
        sr = f"${v_ref:>+10,.0f}"
        sb = f"${v_best:>+10,.0f}"
    elif fmt == '%':
        sr = f"{v_ref:>6.1f}%"
        sb = f"{v_best:>6.1f}%"
    else:
        sr = f"{v_ref:>10.2f}"
        sb = f"{v_best:>10.2f}"
    print(f"  {label:<22}  {sr}  {sb}")

if ref:
    print(f"\n  {'':>22}  {'Ref (15/0)':>12}  {'Best ('+str(best['tb_s'])+'/'+str(best['td_s'])+')':>12}")
    print(f"  {'-' * 55}")
    cmp('Trades', ref['trades'], best['trades'], 'n')
    cmp('Win Rate', ref['wr'], best['wr'], '%')
    cmp('  Long WR', ref['l_wr'], best['l_wr'], '%')
    cmp('  Short WR', ref['s_wr'], best['s_wr'], '%')
    cmp('PnL Total', ref['pnl'], best['pnl'])
    cmp('  Long PnL', ref['l_pnl'], best['l_pnl'])
    cmp('  Short PnL', ref['s_pnl'], best['s_pnl'])
    cmp('Profit Factor', ref['pf'], best['pf'], 'n')
    cmp('Sharpe', ref['sharpe'], best['sharpe'], 'n')
    cmp('Max Drawdown', ref['max_dd'], best['max_dd'])
    cmp('Avg Trade', ref['avg_trade'], best['avg_trade'])
    cmp('  Long Avg', ref['l_avg'], best['l_avg'])
    cmp('  Short Avg', ref['s_avg'], best['s_avg'])

# Test demande : 5 bars, 46 delta
test_5_46 = next((r for r in results if r['tb_s'] == 5 and r['td_s'] == 46), None)
if not test_5_46:
    # Chercher le plus proche
    test_5_46 = next((r for r in results if r['tb_s'] == 5 and abs(r['td_s'] - 46) <= 15), None)

if test_5_46 and ref:
    print(f"\n  {'':>22}  {'Ref (15/0)':>12}  {'Test (5/46)':>12}")
    print(f"  {'-' * 55}")
    cmp('Trades', ref['trades'], test_5_46['trades'], 'n')
    cmp('Win Rate', ref['wr'], test_5_46['wr'], '%')
    cmp('  Short WR', ref['s_wr'], test_5_46['s_wr'], '%')
    cmp('PnL Total', ref['pnl'], test_5_46['pnl'])
    cmp('  Short PnL', ref['s_pnl'], test_5_46['s_pnl'])
    cmp('Profit Factor', ref['pf'], test_5_46['pf'], 'n')
    cmp('Sharpe', ref['sharpe'], test_5_46['sharpe'], 'n')
    cmp('Max Drawdown', ref['max_dd'], test_5_46['max_dd'])

print("\n" + "=" * 80)
