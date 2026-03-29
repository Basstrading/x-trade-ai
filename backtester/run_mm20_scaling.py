"""
TEST SCALING DYNAMIQUE — MM20 Pullback
======================================
Les trades sont identiques, seule la taille varie.
On run le backtest a 1 MNQ ($2/pt) puis on applique un multiplicateur
selon l'etat de l'equity curve.

Methodes testees:
  A) Equity SMA: equity > SMA(N trades) → 4 MNQ, sinon 2 MNQ
  B) DD Threshold: si DD depuis pic > seuil → reduit
  C) Combo: les deux
"""
import sys
from pathlib import Path
import pandas as pd
import numpy as np
import itertools

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backtester.mm20_engine import MM20BacktestEngine

BASE_DIR = Path(__file__).resolve().parent.parent

df = pd.read_csv(BASE_DIR / 'data' / 'databento_nq_5min.csv', index_col='datetime', parse_dates=True)
print(f"Databento NQ 5min: {len(df)} barres\n")

# Run une fois a $2/pt (1 MNQ) pour avoir les trades unitaires
engine = MM20BacktestEngine(
    tp_points=300, trail_bars=15, trail_bars_short=7, trail_delta_short=60,
    max_sl_pts=200, max_trades_day=4, sma_period=20, start_offset_min=30,
    min_sma_dist=0, daily_loss_stop=2, point_value=2.0, daily_loss_usd=250,
    pullback_bars=10, pullback_dist=15,
)
report = engine.run(df)
trades = report.trades
print(f"Trades unitaires: {len(trades)}")

# PnL unitaire par trade (1 MNQ = $2/pt)
unit_pnls = [t['pnl_usd'] for t in trades]
unit_dates = [t['date'] for t in trades]


def simulate_scaling(unit_pnls, unit_dates, method, params):
    """Simule le scaling sur les trades unitaires."""
    equity = 0.0
    peak = 0.0
    scaled_pnls = []
    daily_pnl = {}
    multipliers_used = []

    # Pour equity SMA
    recent_equity = []
    sma_len = params.get('sma_len', 20)
    full_size = params.get('full_size', 4)
    reduced_size = params.get('reduced_size', 2)
    min_size = params.get('min_size', 1)
    dd_thresh_1 = params.get('dd_thresh_1', 500)   # DD seuil pour reduire
    dd_thresh_2 = params.get('dd_thresh_2', 1000)  # DD seuil pour minimum

    for i, (pnl_1, date) in enumerate(zip(unit_pnls, unit_dates)):
        # Determiner le multiplicateur
        dd = equity - peak

        if method == 'sma':
            recent_equity.append(equity)
            if len(recent_equity) >= sma_len:
                sma_val = np.mean(recent_equity[-sma_len:])
                mult = full_size if equity >= sma_val else reduced_size
            else:
                mult = full_size  # pas assez de data, full size

        elif method == 'dd_threshold':
            if dd <= -dd_thresh_2:
                mult = min_size
            elif dd <= -dd_thresh_1:
                mult = reduced_size
            else:
                mult = full_size

        elif method == 'combo':
            # DD threshold + SMA
            recent_equity.append(equity)
            if dd <= -dd_thresh_2:
                mult = min_size
            elif dd <= -dd_thresh_1:
                mult = reduced_size
            else:
                if len(recent_equity) >= sma_len:
                    sma_val = np.mean(recent_equity[-sma_len:])
                    mult = full_size if equity >= sma_val else reduced_size
                else:
                    mult = full_size

        elif method == 'daily_dd':
            # Reset quotidien : si la perte du jour depasse un seuil, reduit
            today_pnl = daily_pnl.get(date, 0)
            if dd <= -dd_thresh_2:
                mult = min_size
            elif dd <= -dd_thresh_1 or today_pnl <= -params.get('daily_cut', 400):
                mult = reduced_size
            else:
                mult = full_size

        else:
            mult = full_size

        multipliers_used.append(mult)
        scaled_pnl = pnl_1 * mult
        scaled_pnls.append(scaled_pnl)
        equity += scaled_pnl
        peak = max(peak, equity)
        daily_pnl[date] = daily_pnl.get(date, 0) + scaled_pnl

    # Stats
    eq_arr = np.cumsum([0] + scaled_pnls)
    peak_arr = np.maximum.accumulate(eq_arr)
    dd_arr = eq_arr - peak_arr
    max_dd = abs(dd_arr.min())

    daily_vals = list(daily_pnl.values())
    worst_day = min(daily_vals) if daily_vals else 0

    wins = [p for p in scaled_pnls if p > 0]
    losses = [p for p in scaled_pnls if p < 0]
    total_pnl = sum(scaled_pnls)
    pf = abs(sum(wins) / sum(losses)) if losses and sum(losses) != 0 else 99
    wr = len(wins) / len(scaled_pnls) * 100 if scaled_pnls else 0
    sharpe = (np.mean(scaled_pnls) / np.std(scaled_pnls) * np.sqrt(252)) if np.std(scaled_pnls) > 0 else 0

    # Topstep check
    blown = False
    blown_day = None
    r_eq = 0.0
    r_peak = 0.0
    for d in sorted(daily_pnl.keys()):
        dpnl = daily_pnl[d]
        if dpnl <= -1000:
            blown = True
            blown_day = d
            break
        r_eq += dpnl
        r_peak = max(r_peak, r_eq)
        if r_eq - r_peak <= -2000:
            blown = True
            blown_day = d
            break

    # Mois positifs
    trades_df = pd.DataFrame({'date': unit_dates, 'pnl': scaled_pnls})
    trades_df['month'] = pd.to_datetime(trades_df['date']).dt.to_period('M')
    months_pos = sum(1 for _, g in trades_df.groupby('month') if g['pnl'].sum() > 0)
    months_total = trades_df['month'].nunique()

    avg_mult = np.mean(multipliers_used)

    return {
        'pnl': round(total_pnl, 0),
        'pf': round(pf, 2),
        'sharpe': round(sharpe, 2),
        'wr': round(wr, 1),
        'max_dd': round(max_dd, 0),
        'worst_day': round(worst_day, 0),
        'blown': blown,
        'blown_day': str(blown_day) if blown_day else None,
        'avg_mult': round(avg_mult, 2),
        'months_pos': months_pos,
        'months_total': months_total,
        'daily_pnl': daily_pnl,
    }


# ============================================================
# References
# ============================================================
ref4 = simulate_scaling(unit_pnls, unit_dates, 'fixed', {'full_size': 4})
ref2 = simulate_scaling(unit_pnls, unit_dates, 'fixed', {'full_size': 2})
ref1 = simulate_scaling(unit_pnls, unit_dates, 'fixed', {'full_size': 1})
print(f"\nREF 4 MNQ fixe: PnL ${ref4['pnl']:+,.0f} | MaxDD ${ref4['max_dd']:,.0f} | Worst day ${ref4['worst_day']:+,.0f} | Blown: {ref4['blown']}")
print(f"REF 2 MNQ fixe: PnL ${ref2['pnl']:+,.0f} | MaxDD ${ref2['max_dd']:,.0f} | Worst day ${ref2['worst_day']:+,.0f} | Blown: {ref2['blown']}")
print(f"REF 1 MNQ fixe: PnL ${ref1['pnl']:+,.0f} | MaxDD ${ref1['max_dd']:,.0f} | Worst day ${ref1['worst_day']:+,.0f} | Blown: {ref1['blown']}")

# ============================================================
# Test methodes — APPROCHE INVERSE: base petite, scale UP
# ============================================================
configs = []

# Methode A: Scale UP — base 1, monte a 2/3/4 quand equity > SMA
for sma_len in [10, 15, 20, 30]:
    for base in [1, 2]:
        for up in [2, 3, 4]:
            if up > base:
                configs.append(('sma', f'SMA({sma_len}) base={base} up={up}', {
                    'sma_len': sma_len, 'full_size': up, 'reduced_size': base,
                }))

# Methode B: DD Threshold — base 2, scale down a 1 si DD, scale up a 3 si OK
for t1 in [200, 300, 500, 800]:
    for t2 in [400, 600, 1000]:
        if t2 > t1:
            for base, hi in [(1, 2), (1, 3), (2, 3), (2, 4)]:
                configs.append(('dd_threshold', f'DD base={base} hi={hi} -{t1}/{base} -{t2}/1', {
                    'full_size': hi, 'reduced_size': base, 'min_size': 1,
                    'dd_thresh_1': t1, 'dd_thresh_2': t2,
                }))

# Methode C: Combo SMA + DD — conservative
for sma_len in [10, 15, 20]:
    for t1 in [300, 500]:
        for t2 in [600, 1000]:
            if t2 > t1:
                for base, hi in [(1, 2), (1, 3), (2, 3)]:
                    configs.append(('combo', f'Combo SMA({sma_len}) b={base}/h={hi} DD-{t1}/-{t2}', {
                        'sma_len': sma_len, 'full_size': hi, 'reduced_size': base, 'min_size': 1,
                        'dd_thresh_1': t1, 'dd_thresh_2': t2,
                    }))

# Methode D: Daily DD — ajoute cut journalier
for t1 in [300, 500]:
    for t2 in [600, 1000]:
        for dc in [200, 400]:
            if t2 > t1:
                for base, hi in [(1, 2), (1, 3), (2, 3)]:
                    configs.append(('daily_dd', f'DailyDD b={base}/h={hi} t1={t1} t2={t2} dc={dc}', {
                        'full_size': hi, 'reduced_size': base, 'min_size': 1,
                        'dd_thresh_1': t1, 'dd_thresh_2': t2, 'daily_cut': dc,
                    }))

print(f"\nTest {len(configs)} configurations de scaling...\n")

results = []
for method, label, params in configs:
    r = simulate_scaling(unit_pnls, unit_dates, method, params)
    r['method'] = method
    r['label'] = label
    results.append(r)

# Trier par: Topstep compliant d'abord, puis PnL
compliant = [r for r in results if not r['blown']]
non_compliant = [r for r in results if r['blown']]

compliant.sort(key=lambda x: x['pnl'], reverse=True)
non_compliant.sort(key=lambda x: x['pnl'], reverse=True)

print("=" * 120)
print("  TOPSTEP COMPLIANT (MaxDD < $2000 ET Daily < $1000)")
print("=" * 120)

if compliant:
    print(f"  {'#':>2}  {'Config':<40}  {'PnL':>11}  {'PF':>5}  {'Sharpe':>6}  {'MaxDD':>8}  {'Worst D':>8}  {'Avg Mult':>8}  {'M+ %':>5}")
    print("-" * 120)
    for i, r in enumerate(compliant[:20]):
        mp = f"{r['months_pos']}/{r['months_total']}"
        print(f"  {i+1:>2}  {r['label']:<40}  ${r['pnl']:>+9,.0f}  {r['pf']:>4.2f}  {r['sharpe']:>5.2f}  ${r['max_dd']:>7,.0f}  ${r['worst_day']:>+7,.0f}  {r['avg_mult']:>7.2f}  {mp:>5}")
else:
    print("  Aucune configuration Topstep compliant trouvee.")

print(f"\n  ({len(non_compliant)} configs non-compliantes)")

# Top 5 non-compliantes pour comparaison
if non_compliant:
    print(f"\n  TOP 5 NON-COMPLIANTES (meilleur PnL):")
    print(f"  {'#':>2}  {'Config':<40}  {'PnL':>11}  {'PF':>5}  {'MaxDD':>8}  {'Worst D':>8}  {'Blown':>12}")
    for i, r in enumerate(non_compliant[:5]):
        print(f"  {i+1:>2}  {r['label']:<40}  ${r['pnl']:>+9,.0f}  {r['pf']:>4.2f}  ${r['max_dd']:>7,.0f}  ${r['worst_day']:>+7,.0f}  {r['blown_day']:>12}")

# ============================================================
# Comparaison best compliant vs reference
# ============================================================
if compliant:
    best = compliant[0]
    print("\n" + "=" * 80)
    print("  REFERENCE vs BEST TOPSTEP COMPLIANT")
    print("=" * 80)
    print(f"  {'':>22}  {'Ref (4 fixe)':>14}  {'Ref (2 fixe)':>14}  {'Best scaling':>14}")
    print(f"  {'-' * 70}")
    print(f"  {'PnL Total':<22}  ${ref4['pnl']:>+12,.0f}  ${ref2['pnl']:>+12,.0f}  ${best['pnl']:>+12,.0f}")
    print(f"  {'Profit Factor':<22}  {ref4['pf']:>13.2f}  {ref2['pf']:>13.2f}  {best['pf']:>13.2f}")
    print(f"  {'Sharpe':<22}  {ref4['sharpe']:>13.2f}  {ref2['sharpe']:>13.2f}  {best['sharpe']:>13.2f}")
    print(f"  {'Max Drawdown':<22}  ${ref4['max_dd']:>+12,.0f}  ${ref2['max_dd']:>+12,.0f}  ${best['max_dd']:>+12,.0f}")
    print(f"  {'Pire jour':<22}  ${ref4['worst_day']:>+12,.0f}  ${ref2['worst_day']:>+12,.0f}  ${best['worst_day']:>+12,.0f}")
    print(f"  {'Topstep compliant':<22}  {'NON':>14}  {'?':>14}  {'OUI':>14}")
    print(f"  {'Mois positifs':<22}  {ref4['months_pos']}/{ref4['months_total']:>11}  {ref2['months_pos']}/{ref2['months_total']:>11}  {best['months_pos']}/{best['months_total']:>11}")
    print(f"  {'Taille moyenne':<22}  {'4.00 MNQ':>14}  {'2.00 MNQ':>14}  {best['avg_mult']:.2f} MNQ")
    print(f"  {'Projection/mois':<22}  ${ref4['pnl']/30:>+12,.0f}  ${ref2['pnl']/30:>+12,.0f}  ${best['pnl']/30:>+12,.0f}")
    print(f"\n  Config: {best['label']}")

print("\n" + "=" * 80)

# Save
import json
out = {
    'reference': ref4,
    'compliant': [{k: v for k, v in r.items() if k != 'daily_pnl'} for r in compliant[:10]],
    'best_config': compliant[0]['label'] if compliant else None,
}
outpath = BASE_DIR / 'data' / 'mm20_scaling_results.json'
outpath.write_text(json.dumps(out, default=str, ensure_ascii=False, indent=2), encoding='utf-8')
print(f"Sauvegarde: {outpath}")
