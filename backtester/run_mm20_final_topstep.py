"""
BACKTEST MM20 PULLBACK + SHORT STOP 7/60 — Topstep $50K — 2.5 ANS
==================================================================
Config finale:
  - 4 MNQ ($8/pt)
  - Pullback: 10 bars, 15 pts
  - Longs: trailing 15 bars, 0 delta
  - Shorts: trailing 7 bars, +60 pts delta
  - Daily loss cap $1000
  - Topstep $50K: MaxDD $2000, Daily loss $1000
"""
import sys
from pathlib import Path
import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backtester.mm20_engine import MM20BacktestEngine

BASE_DIR = Path(__file__).resolve().parent.parent
POINT_VALUE = 8.0
TOPSTEP_MAX_DD = 2000
TOPSTEP_DAILY_LOSS = 1000

df = pd.read_csv(BASE_DIR / 'data' / 'databento_nq_5min.csv', index_col='datetime', parse_dates=True)
print(f"Databento NQ 5min: {len(df)} barres, {df.index.min().date()} -> {df.index.max().date()}")

engine = MM20BacktestEngine(
    tp_points=300,
    trail_bars=15,
    trail_bars_short=7,
    trail_delta_short=60,
    max_trades_day=4,
    sma_period=20,
    start_offset_min=30,
    min_sma_dist=0,
    atr_min=0,
    daily_loss_stop=2,
    point_value=POINT_VALUE,
    daily_loss_usd=TOPSTEP_DAILY_LOSS,
    pullback_bars=10,
    pullback_dist=15,
    max_sl_pts=200,
)

report = engine.run(df)

if not report:
    print("Aucun trade genere.")
    sys.exit(1)

# ============================================================
# Analyse
# ============================================================
equity = report.equity_curve
eq = np.array(equity)
peak = np.maximum.accumulate(eq)
drawdowns = eq - peak
max_trailing_dd = abs(drawdowns.min())

daily = report.daily_pnl
daily_values = list(daily.values())
worst_day = min(daily_values) if daily_values else 0
best_day = max(daily_values) if daily_values else 0
days_positive = sum(1 for v in daily_values if v > 0)
days_negative = sum(1 for v in daily_values if v < 0)

# Topstep check jour par jour
blown = False
blown_day = None
blown_reason = ''
running_peak = 0.0
running_equity = 0.0
for d, pnl in sorted(daily.items()):
    # Check daily loss AVANT de l'ajouter
    if pnl <= -TOPSTEP_DAILY_LOSS:
        blown = True
        blown_day = d
        blown_reason = f'daily loss ${pnl:+,.0f}'
        break
    running_equity += pnl
    running_peak = max(running_peak, running_equity)
    trailing_dd = running_equity - running_peak
    if trailing_dd <= -TOPSTEP_MAX_DD:
        blown = True
        blown_day = d
        blown_reason = f'trailing DD ${trailing_dd:+,.0f}'
        break

# Stats longs vs shorts
longs = [t for t in report.trades if t['direction'] == 'long']
shorts = [t for t in report.trades if t['direction'] == 'short']
l_pnls = [t['pnl_usd'] for t in longs]
s_pnls = [t['pnl_usd'] for t in shorts]
l_wr = len([p for p in l_pnls if p > 0]) / len(l_pnls) * 100 if l_pnls else 0
s_wr = len([p for p in s_pnls if p > 0]) / len(s_pnls) * 100 if s_pnls else 0

# ============================================================
# Affichage
# ============================================================
print("\n" + "=" * 70)
print("  MM20 PULLBACK + SHORT 7/60 - TOPSTEP $50K - 2.5 ANS")
print("=" * 70)

print(f"\n  Periode       : {report.trades[0]['date']} -> {report.trades[-1]['date']}")
print(f"  Duree         : {len(daily_values)} jours de trading")
print(f"  Instrument    : MNQ x4 ($8/pt)")

print(f"\n  {'-' * 55}")
print(f"  RESULTATS")
print(f"  {'-' * 55}")
print(f"  Trades        : {report.total_trades}  ({len(longs)}L / {len(shorts)}S)")
print(f"  Win Rate      : {report.win_rate}%  (L:{l_wr:.1f}% / S:{s_wr:.1f}%)")
print(f"  PnL Total     : ${report.total_pnl_usd:+,.0f}")
print(f"    Longs       : ${sum(l_pnls):+,.0f}")
print(f"    Shorts      : ${sum(s_pnls):+,.0f}")
print(f"  Profit Factor : {report.profit_factor}")
print(f"  Sharpe Ratio  : {report.sharpe_ratio}")
print(f"  Avg Trade     : ${report.avg_trade:+,.0f}")
print(f"  Avg Win       : ${report.avg_win:+,.0f}")
print(f"  Avg Loss      : ${report.avg_loss:+,.0f}")
print(f"  Best Trade    : ${report.best_trade:+,.0f}")
print(f"  Worst Trade   : ${report.worst_trade:+,.0f}")

print(f"\n  {'-' * 55}")
print(f"  COMPLIANCE TOPSTEP $50K")
print(f"  {'-' * 55}")
dd_ok = max_trailing_dd < TOPSTEP_MAX_DD
daily_ok = abs(worst_day) < TOPSTEP_DAILY_LOSS
print(f"  Max Trailing DD   : ${max_trailing_dd:,.0f}  (limite $2,000) [{'OK' if dd_ok else 'DEPASSE'}]")
print(f"  Pire journee      : ${worst_day:+,.0f}  (limite -$1,000) [{'OK' if daily_ok else 'DEPASSE'}]")
print(f"  Compte elimine ?  : {'OUI — ' + blown_reason + ' le ' + str(blown_day) if blown else 'NON'}")

if dd_ok and daily_ok and not blown:
    print(f"\n  >>> STRATEGIE 100% TOPSTEP COMPLIANT SUR 2.5 ANS <<<")

print(f"\n  {'-' * 55}")
print(f"  STATS JOURNALIERES")
print(f"  {'-' * 55}")
print(f"  Jours positifs  : {days_positive}")
print(f"  Jours negatifs  : {days_negative}")
print(f"  Jours flat      : {len(daily_values) - days_positive - days_negative}")
print(f"  Meilleur jour   : ${best_day:+,.0f}")
print(f"  Pire jour       : ${worst_day:+,.0f}")
print(f"  Avg jour        : ${np.mean(daily_values):+,.0f}")

monthly_proj = np.mean(daily_values) * 20
print(f"\n  Projection mensuelle (20j) : ${monthly_proj:+,.0f}")
print(f"  ROI annuel sur $50K : {report.total_pnl_usd / (len(daily_values)/252) / 50000 * 100:+.0f}%")

# Trimestres
print(f"\n  {'-' * 55}")
print(f"  BREAKDOWN PAR TRIMESTRE")
print(f"  {'-' * 55}")

trades_df = pd.DataFrame(report.trades)
trades_df['date_parsed'] = pd.to_datetime(trades_df['date'])
trades_df['quarter'] = trades_df['date_parsed'].dt.to_period('Q')

for q, group in trades_df.groupby('quarter'):
    q_pnls = group['pnl_usd'].tolist()
    q_wins = [p for p in q_pnls if p > 0]
    q_losses = [p for p in q_pnls if p < 0]
    q_wr = len(q_wins) / len(q_pnls) * 100 if q_pnls else 0
    q_pnl = sum(q_pnls)
    q_pf = abs(sum(q_wins) / sum(q_losses)) if q_losses and sum(q_losses) != 0 else 99
    print(f"  {q}  |  {len(q_pnls):>3} trades  |  WR {q_wr:5.1f}%  |  PnL ${q_pnl:>+9,.0f}  |  PF {q_pf:.2f}")

# Mois
print(f"\n  {'-' * 55}")
print(f"  BREAKDOWN PAR MOIS")
print(f"  {'-' * 55}")

trades_df['month'] = trades_df['date_parsed'].dt.to_period('M')
months_pos = 0
months_neg = 0
for m, group in trades_df.groupby('month'):
    m_pnl = group['pnl_usd'].sum()
    m_trades = len(group)
    m_wr = len(group[group['pnl_usd'] > 0]) / m_trades * 100 if m_trades else 0
    if m_pnl > 0:
        months_pos += 1
    else:
        months_neg += 1
    marker = "+" if m_pnl > 0 else "-"
    print(f"  {m}  |  {m_trades:>3} trades  |  WR {m_wr:5.1f}%  |  PnL ${m_pnl:>+9,.0f}  {marker}")

pct = months_pos / (months_pos + months_neg) * 100
print(f"\n  Mois positifs: {months_pos} / {months_pos + months_neg}  ({pct:.0f}%)")

# Detail jour par jour
print(f"\n  {'-' * 55}")
print(f"  EQUITY JOUR PAR JOUR")
print(f"  {'-' * 55}")
print(f"  {'Date':<12}  {'PnL':>10}  {'Cumul':>10}  {'Peak':>10}  {'DD':>10}  {'Trades':>7}")
print(f"  {'-' * 65}")

trades_per_day = {}
for t in report.trades:
    d = t['date']
    trades_per_day[d] = trades_per_day.get(d, 0) + 1

cumul = 0.0
peak_d = 0.0
for d in sorted(daily.keys()):
    pnl = daily[d]
    cumul += pnl
    peak_d = max(peak_d, cumul)
    dd = cumul - peak_d
    n_trades = trades_per_day.get(d, 0)
    warn = " !" if dd <= -TOPSTEP_MAX_DD or pnl <= -TOPSTEP_DAILY_LOSS else ""
    print(f"  {d:<12}  ${pnl:>+9,.0f}  ${cumul:>+9,.0f}  ${peak_d:>+9,.0f}  ${dd:>+9,.0f}  {n_trades:>5}{warn}")

print("=" * 70)

# Save
import json
out = {
    'config': 'MM20 Pullback + Short 7/60 - Topstep $50K - 2.5 ans',
    'params': {
        'tp_points': 300, 'trail_bars_long': 15, 'trail_bars_short': 7,
        'trail_delta_short': 60, 'pullback_bars': 10, 'pullback_dist': 15,
        'start_offset_min': 30, 'daily_loss_stop': 2, 'daily_loss_usd': 1000,
        'max_trades_day': 4, 'point_value': 8.0,
    },
    'results': {
        'trades': report.total_trades, 'wr': report.win_rate,
        'pnl': report.total_pnl_usd, 'pf': report.profit_factor,
        'sharpe': report.sharpe_ratio, 'max_dd': report.max_drawdown_usd,
        'max_trailing_dd': round(max_trailing_dd, 2),
    },
    'compliance': {
        'dd_ok': dd_ok, 'daily_ok': daily_ok, 'blown': blown,
        'blown_day': str(blown_day) if blown_day else None,
        'blown_reason': blown_reason,
    },
    'monthly': {
        'months_positive': months_pos, 'months_negative': months_neg,
        'pct_positive': round(pct, 1),
    },
    'daily_pnl': {str(k): round(v, 2) for k, v in daily.items()},
    'equity_curve': equity,
}
outpath = BASE_DIR / 'data' / 'mm20_final_topstep.json'
outpath.write_text(json.dumps(out, default=str, ensure_ascii=False, indent=2), encoding='utf-8')
print(f"\nSauvegarde: {outpath}")
