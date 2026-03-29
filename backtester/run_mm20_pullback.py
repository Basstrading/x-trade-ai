"""
BACKTEST MM20 PULLBACK — Databento NQ 5min — 2.5 ANS
=====================================================
Strategie : on entre uniquement quand le prix a fait un pullback
vers la SMA20 recemment (meilleur point d'entree).

Teste plusieurs combinaisons pullback_bars / pullback_dist
puis affiche les meilleurs resultats.
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

# Charger
df = pd.read_csv(BASE_DIR / 'data' / 'databento_nq_5min.csv', index_col='datetime', parse_dates=True)
print(f"Databento NQ 5min: {len(df)} barres, {df.index.min().date()} -> {df.index.max().date()}")

# ============================================================
# Grid search pullback params
# ============================================================
pullback_bars_grid = [3, 5, 8, 10, 15]
pullback_dist_grid = [0, 5, 10, 15, 20, 30]

# Garder les meilleurs params MM20 pour le reste
BASE_PARAMS = dict(
    tp_points=300,
    trail_bars=15,
    max_trades_day=4,
    sma_period=20,
    start_offset_min=30,
    min_sma_dist=0,       # desactive car le pullback fait office de filtre
    atr_min=0,
    daily_loss_stop=2,
    point_value=POINT_VALUE,
    daily_loss_usd=1000,
)

results = []
total = len(pullback_bars_grid) * len(pullback_dist_grid)
print(f"\nOptimisation pullback: {total} combinaisons...")
t0 = time.time()

for idx, (pb_bars, pb_dist) in enumerate(itertools.product(pullback_bars_grid, pullback_dist_grid)):
    engine = MM20BacktestEngine(
        **BASE_PARAMS,
        pullback_bars=pb_bars,
        pullback_dist=pb_dist,
    )
    report = engine.run(df)

    if report and report.total_trades >= 50:
        eq = np.array(report.equity_curve)
        peak = np.maximum.accumulate(eq)
        max_dd = abs((eq - peak).min())

        # Score composite
        pf = report.profit_factor
        pnl = report.total_pnl_usd
        score = (pnl * pf / max_dd) if max_dd > 0 else 0

        results.append({
            'pb_bars': pb_bars,
            'pb_dist': pb_dist,
            'trades': report.total_trades,
            'wr': report.win_rate,
            'pnl': pnl,
            'pf': pf,
            'sharpe': report.sharpe_ratio,
            'max_dd': max_dd,
            'avg_trade': report.avg_trade,
            'avg_win': report.avg_win,
            'avg_loss': report.avg_loss,
            'score': score,
            'report': report,
        })

    if (idx + 1) % 10 == 0:
        elapsed = time.time() - t0
        print(f"  {idx+1}/{total} ({elapsed:.0f}s)")

elapsed = time.time() - t0
print(f"Termine en {elapsed:.0f}s — {len(results)} combinaisons valides\n")

# Trier par score
results.sort(key=lambda x: x['score'], reverse=True)

# ============================================================
# Afficher top 10
# ============================================================
print("=" * 100)
print("  TOP 10 PULLBACK CONFIGURATIONS")
print("=" * 100)
print(f"  {'#':>2}  {'PB bars':>7}  {'PB dist':>7}  {'Trades':>7}  {'WR':>6}  {'PnL':>12}  {'PF':>6}  {'Sharpe':>7}  {'MaxDD':>10}  {'Avg Trade':>10}  {'Score':>10}")
print("-" * 100)

for i, r in enumerate(results[:10]):
    print(f"  {i+1:>2}  {r['pb_bars']:>7}  {r['pb_dist']:>7}  {r['trades']:>7}  {r['wr']:>5.1f}%  ${r['pnl']:>+10,.0f}  {r['pf']:>5.2f}  {r['sharpe']:>6.2f}  ${r['max_dd']:>9,.0f}  ${r['avg_trade']:>+9,.0f}  {r['score']:>10,.0f}")

# ============================================================
# Comparer le best pullback vs MM20 classique (sans pullback)
# ============================================================
print("\n" + "=" * 100)
print("  COMPARAISON : MM20 CLASSIQUE vs MM20 PULLBACK (BEST)")
print("=" * 100)

# MM20 classique (memes params, sans pullback)
classic_params = dict(BASE_PARAMS)
classic_params['min_sma_dist'] = 20  # remet le filtre original
engine_classic = MM20BacktestEngine(
    **classic_params,
    pullback_bars=0,
    pullback_dist=0,
)
report_classic = engine_classic.run(df)

best = results[0]
report_pb = best['report']

eq_c = np.array(report_classic.equity_curve)
dd_c = abs((eq_c - np.maximum.accumulate(eq_c)).min())

daily_c = report_classic.daily_pnl
daily_pb = report_pb.daily_pnl
months_pos_c = 0
months_neg_c = 0
months_pos_pb = 0
months_neg_pb = 0

# Monthly PnL for classic
trades_c = pd.DataFrame(report_classic.trades)
trades_c['date_parsed'] = pd.to_datetime(trades_c['date'])
trades_c['month'] = trades_c['date_parsed'].dt.to_period('M')
for m, g in trades_c.groupby('month'):
    if g['pnl_usd'].sum() > 0:
        months_pos_c += 1
    else:
        months_neg_c += 1

trades_pb = pd.DataFrame(report_pb.trades)
trades_pb['date_parsed'] = pd.to_datetime(trades_pb['date'])
trades_pb['month'] = trades_pb['date_parsed'].dt.to_period('M')
for m, g in trades_pb.groupby('month'):
    if g['pnl_usd'].sum() > 0:
        months_pos_pb += 1
    else:
        months_neg_pb += 1

def pr(label, val_c, val_pb, fmt='$', better='high'):
    if fmt == '$':
        sc = f"${val_c:>+10,.0f}"
        sp = f"${val_pb:>+10,.0f}"
    elif fmt == '%':
        sc = f"{val_c:>6.1f}%"
        sp = f"{val_pb:>6.1f}%"
    else:
        sc = f"{val_c:>10.2f}"
        sp = f"{val_pb:>10.2f}"

    if better == 'high':
        mc = ' *' if val_c > val_pb else '  '
        mp = ' *' if val_pb > val_c else '  '
    else:
        mc = ' *' if val_c < val_pb else '  '
        mp = ' *' if val_pb < val_c else '  '
    print(f"  {label:<20}  {sc}{mc}  {sp}{mp}")

print(f"\n  {'Metrique':<20}  {'MM20 Classique':>13}  {'MM20 Pullback':>13}")
print(f"  {'-' * 55}")
pr('Trades', report_classic.total_trades, report_pb.total_trades, 'n', 'high')
pr('Win Rate', report_classic.win_rate, report_pb.win_rate, '%', 'high')
pr('PnL Total', report_classic.total_pnl_usd, report_pb.total_pnl_usd, '$', 'high')
pr('Profit Factor', report_classic.profit_factor, report_pb.profit_factor, 'n', 'high')
pr('Sharpe Ratio', report_classic.sharpe_ratio, report_pb.sharpe_ratio, 'n', 'high')
pr('Max Drawdown', dd_c, best['max_dd'], '$', 'low')
pr('Avg Trade', report_classic.avg_trade, report_pb.avg_trade, '$', 'high')
pr('Avg Win', report_classic.avg_win, report_pb.avg_win, '$', 'high')
pr('Avg Loss', report_classic.avg_loss, report_pb.avg_loss, '$', 'low')
pr('Best Trade', report_classic.best_trade, report_pb.best_trade, '$', 'high')
pr('Worst Trade', report_classic.worst_trade, report_pb.worst_trade, '$', 'low')

pct_c = months_pos_c / (months_pos_c + months_neg_c) * 100
pct_pb = months_pos_pb / (months_pos_pb + months_neg_pb) * 100
pr('Mois positifs %', pct_c, pct_pb, '%', 'high')

print(f"\n  Best pullback config: pb_bars={best['pb_bars']}, pb_dist={best['pb_dist']}")

# Breakdown par trimestre du best pullback
print(f"\n  {'-' * 50}")
print(f"  BREAKDOWN PAR TRIMESTRE (Pullback)")
print(f"  {'-' * 50}")

trades_pb['quarter'] = trades_pb['date_parsed'].dt.to_period('Q')
for q, group in trades_pb.groupby('quarter'):
    q_pnls = group['pnl_usd'].tolist()
    q_wins = [p for p in q_pnls if p > 0]
    q_losses = [p for p in q_pnls if p < 0]
    q_wr = len(q_wins) / len(q_pnls) * 100 if q_pnls else 0
    q_pnl = sum(q_pnls)
    q_pf = abs(sum(q_wins) / sum(q_losses)) if q_losses and sum(q_losses) != 0 else 99
    print(f"  {q}  |  {len(q_pnls):>3} trades  |  WR {q_wr:5.1f}%  |  PnL ${q_pnl:>+9,.0f}  |  PF {q_pf:.2f}")

# Mois
print(f"\n  {'-' * 50}")
print(f"  BREAKDOWN PAR MOIS (Pullback)")
print(f"  {'-' * 50}")
for m, group in trades_pb.groupby('month'):
    m_pnl = group['pnl_usd'].sum()
    m_trades = len(group)
    m_wr = len(group[group['pnl_usd'] > 0]) / m_trades * 100 if m_trades else 0
    marker = "+" if m_pnl > 0 else "-"
    print(f"  {m}  |  {m_trades:>3} trades  |  WR {m_wr:5.1f}%  |  PnL ${m_pnl:>+9,.0f}  {marker}")

print("=" * 100)

# Save
import json
out = {
    'best_config': {'pb_bars': best['pb_bars'], 'pb_dist': best['pb_dist']},
    'top10': [{k: v for k, v in r.items() if k != 'report'} for r in results[:10]],
    'comparison': {
        'classic': {
            'trades': report_classic.total_trades,
            'wr': report_classic.win_rate,
            'pnl': report_classic.total_pnl_usd,
            'pf': report_classic.profit_factor,
            'sharpe': report_classic.sharpe_ratio,
            'max_dd': round(dd_c, 2),
        },
        'pullback': {
            'trades': report_pb.total_trades,
            'wr': report_pb.win_rate,
            'pnl': report_pb.total_pnl_usd,
            'pf': report_pb.profit_factor,
            'sharpe': report_pb.sharpe_ratio,
            'max_dd': round(best['max_dd'], 2),
        }
    }
}
outpath = BASE_DIR / 'data' / 'mm20_pullback_results.json'
outpath.write_text(json.dumps(out, default=str, ensure_ascii=False, indent=2), encoding='utf-8')
print(f"\nSauvegarde: {outpath}")
