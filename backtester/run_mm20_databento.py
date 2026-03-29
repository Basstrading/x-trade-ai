"""
BACKTEST MM20 Optimise — Databento NQ 5min — 2.5 ANS
=====================================================
Vraies donnees 5min NQ continu depuis Databento.
Config: 4 MNQ ($8/pt), daily loss cap $1000, Topstep $50K
"""
import sys
from pathlib import Path
import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backtester.mm20_engine import MM20BacktestEngine

BASE_DIR = Path(__file__).resolve().parent.parent
POINT_VALUE = 8.0  # 4 MNQ x $2/pt

# ============================================================
# Charger les donnees Databento 5min
# ============================================================
df = pd.read_csv(BASE_DIR / 'data' / 'databento_nq_5min.csv', index_col='datetime', parse_dates=True)
print(f"Databento NQ 5min: {len(df)} barres, {df.index.min().date()} -> {df.index.max().date()}")

# ============================================================
# Run MM20 Optimise — identique a la config 90 jours
# ============================================================
engine = MM20BacktestEngine(
    tp_points=300,
    trail_bars=15,
    max_trades_day=4,
    sma_period=20,
    start_offset_min=30,
    min_sma_dist=20,
    atr_min=0,
    daily_loss_stop=2,
    point_value=POINT_VALUE,
    daily_loss_usd=1000,
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

pnls = [t['pnl_usd'] for t in report.trades]
wins = [p for p in pnls if p > 0]
losses = [p for p in pnls if p < 0]

# Topstep compliance check
TOPSTEP_MAX_DD = 2000
TOPSTEP_DAILY_LOSS = 1000
blown = False
blown_day = None
running_peak = 0.0
running_equity = 0.0
for d, pnl in sorted(daily.items()):
    running_equity += pnl
    running_peak = max(running_peak, running_equity)
    trailing_dd = running_equity - running_peak
    if trailing_dd <= -TOPSTEP_MAX_DD:
        blown = True
        blown_day = d
        break
    if pnl <= -TOPSTEP_DAILY_LOSS:
        blown = True
        blown_day = d
        break

# ============================================================
# Affichage
# ============================================================
print("\n" + "=" * 70)
print("  MM20 OPTIMISE - DATABENTO 5min - 4 MNQ ($8/pt) - 2.5 ANS")
print("=" * 70)

print(f"\n  Periode       : {report.trades[0]['date']} -> {report.trades[-1]['date']}")
print(f"  Duree         : {len(daily_values)} jours de trading")
print(f"  Instrument    : MNQ x4 ($8/pt)")

print(f"\n  {'-' * 50}")
print(f"  RESULTATS")
print(f"  {'-' * 50}")
print(f"  Trades        : {report.total_trades}")
print(f"  Win Rate      : {report.win_rate}%")
print(f"  PnL Total     : ${report.total_pnl_usd:+,.0f}")
print(f"  Profit Factor : {report.profit_factor}")
print(f"  Sharpe Ratio  : {report.sharpe_ratio}")
print(f"  Avg Trade     : ${report.avg_trade:+,.0f}")
print(f"  Avg Win       : ${report.avg_win:+,.0f}")
print(f"  Avg Loss      : ${report.avg_loss:+,.0f}")
print(f"  Best Trade    : ${report.best_trade:+,.0f}")
print(f"  Worst Trade   : ${report.worst_trade:+,.0f}")
print(f"  Max Trailing DD : ${max_trailing_dd:,.0f}")

print(f"\n  {'-' * 50}")
print(f"  COMPLIANCE TOPSTEP $50K")
print(f"  {'-' * 50}")
dd_status = "OK" if max_trailing_dd < TOPSTEP_MAX_DD else "DEPASSE"
print(f"  Max Trailing DD     : ${max_trailing_dd:,.0f}  (limite: ${TOPSTEP_MAX_DD:,}) [{dd_status}]")
daily_status = "OK" if abs(worst_day) < TOPSTEP_DAILY_LOSS else "DEPASSE"
print(f"  Pire journee        : ${worst_day:+,.0f}  (limite: -${TOPSTEP_DAILY_LOSS:,}) [{daily_status}]")
print(f"  Compte elimine ?    : {'OUI (jour: ' + str(blown_day) + ')' if blown else 'NON'}")

print(f"\n  {'-' * 50}")
print(f"  STATS JOURNALIERES")
print(f"  {'-' * 50}")
print(f"  Jours positifs  : {days_positive}")
print(f"  Jours negatifs  : {days_negative}")
print(f"  Meilleur jour   : ${best_day:+,.0f}")
print(f"  Pire jour       : ${worst_day:+,.0f}")
print(f"  Avg jour        : ${np.mean(daily_values):+,.0f}")

avg_daily = np.mean(daily_values)
monthly_proj = avg_daily * 20
total_months = len(daily_values) / 20
print(f"\n  Projection mensuelle (20j) : ${monthly_proj:+,.0f}")
print(f"  Duree en mois   : {total_months:.1f}")

# Breakdown par trimestre
print(f"\n  {'-' * 50}")
print(f"  BREAKDOWN PAR TRIMESTRE")
print(f"  {'-' * 50}")

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

# Mois par mois
print(f"\n  {'-' * 50}")
print(f"  BREAKDOWN PAR MOIS")
print(f"  {'-' * 50}")

trades_df['month'] = trades_df['date_parsed'].dt.to_period('M')
months_positive = 0
months_negative = 0

for m, group in trades_df.groupby('month'):
    m_pnl = group['pnl_usd'].sum()
    m_trades = len(group)
    m_wr = len(group[group['pnl_usd'] > 0]) / m_trades * 100 if m_trades else 0
    if m_pnl > 0:
        months_positive += 1
    else:
        months_negative += 1
    marker = "+" if m_pnl > 0 else "-"
    print(f"  {m}  |  {m_trades:>3} trades  |  WR {m_wr:5.1f}%  |  PnL ${m_pnl:>+9,.0f}  {marker}")

pct_pos = months_positive / (months_positive + months_negative) * 100 if (months_positive + months_negative) > 0 else 0
print(f"\n  Mois positifs: {months_positive} / {months_positive + months_negative}  ({pct_pos:.0f}%)")

print("=" * 70)

# Sauvegarder
import json
out = {
    'config': 'MM20 Optimise - Databento 5min - 4 MNQ - 2.5 ans',
    'source': 'Databento NQ.c.0 ohlcv-1m resampled 5min',
    'point_value': POINT_VALUE,
    'total_trades': report.total_trades,
    'win_rate': report.win_rate,
    'total_pnl_usd': report.total_pnl_usd,
    'profit_factor': report.profit_factor,
    'sharpe_ratio': report.sharpe_ratio,
    'max_drawdown_usd': report.max_drawdown_usd,
    'max_trailing_dd': round(max_trailing_dd, 2),
    'avg_trade': report.avg_trade,
    'compliance': {
        'topstep_dd_ok': max_trailing_dd < TOPSTEP_MAX_DD,
        'topstep_daily_ok': abs(worst_day) < TOPSTEP_DAILY_LOSS,
        'blown': blown,
        'blown_day': str(blown_day) if blown_day else None,
    },
    'months_positive': months_positive,
    'months_negative': months_negative,
    'daily_pnl': {str(k): round(v, 2) for k, v in daily.items()},
    'equity_curve': equity,
}
outpath = BASE_DIR / 'data' / 'mm20_databento_longterm.json'
outpath.write_text(json.dumps(out, default=str, ensure_ascii=False, indent=2), encoding='utf-8')
print(f"\nSauvegarde: {outpath}")
