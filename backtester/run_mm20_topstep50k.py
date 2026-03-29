"""
BACKTEST MM20 Optimise — MNQ x4 — Topstep $50K
================================================
Config:
  - 4 Micro NQ (MNQ) = $2/pt x 4 = $8/pt
  - Topstep $50K : Max Drawdown $2,000 / Max Daily Loss $1,000
  - Strategie MM20 optimisee : TP 300pts, trail 15 bars, start +30min,
    min SMA dist 20pts, daily loss stop 2
"""
import sys
from pathlib import Path
import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backtester.mm20_engine import MM20BacktestEngine

# --- Config ---
POINT_VALUE = 8.0  # 4 MNQ x $2/pt
TOPSTEP_MAX_DD = 2000  # Trailing drawdown limit
TOPSTEP_DAILY_LOSS = 1000  # Max daily loss

# Charger les donnees
cache = Path(__file__).resolve().parent.parent / 'data' / 'cache_5min.csv'
df = pd.read_csv(cache, index_col='datetime', parse_dates=True)

DAILY_LOSS_CAP = 1000  # Arrete de trader si perte jour >= $1000

# MM20 Optimise avec MNQ x4 + daily loss cap
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
    daily_loss_usd=DAILY_LOSS_CAP,
)
report = engine.run(df)

if not report:
    print("Aucun trade genere.")
    sys.exit(1)

# --- Analyse Topstep compliance ---
equity = report.equity_curve
eq = np.array(equity)
peak = np.maximum.accumulate(eq)
drawdowns = eq - peak

# Trailing drawdown (max peak-to-trough)
max_trailing_dd = abs(drawdowns.min())

# Daily PnL analysis
daily = report.daily_pnl
daily_values = list(daily.values())
worst_day = min(daily_values) if daily_values else 0
best_day = max(daily_values) if daily_values else 0
days_positive = sum(1 for v in daily_values if v > 0)
days_negative = sum(1 for v in daily_values if v < 0)
days_flat = sum(1 for v in daily_values if v == 0)

# Check si on aurait ete elimine
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

# Wins/losses
pnls = [t['pnl_usd'] for t in report.trades]
wins = [p for p in pnls if p > 0]
losses = [p for p in pnls if p < 0]

# --- Affichage ---
print("\n" + "=" * 70)
print("  MM20 OPTIMISE - 4 MNQ ($8/pt) - TOPSTEP $50K - Cap $800/jour")
print("=" * 70)

print(f"\n  Periode       : {report.trades[0]['date']} -> {report.trades[-1]['date']}")
print(f"  Instrument    : MNQ x4 ($8/pt)")
print(f"  Compte        : Topstep $50,000")

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

print(f"\n  {'-' * 50}")
print(f"  COMPLIANCE TOPSTEP $50K")
print(f"  {'-' * 50}")
print(f"  Max Trailing DD     : ${max_trailing_dd:,.0f}  (limite: ${TOPSTEP_MAX_DD:,})")
dd_status = "OK" if max_trailing_dd < TOPSTEP_MAX_DD else "DEPASSE"
print(f"  Status DD           : {dd_status}")
print(f"  Pire journee        : ${worst_day:+,.0f}  (limite: -${TOPSTEP_DAILY_LOSS:,})")
daily_status = "OK" if abs(worst_day) < TOPSTEP_DAILY_LOSS else "DEPASSE"
print(f"  Status Daily Loss   : {daily_status}")
print(f"  Compte elimine ?    : {'OUI (jour: ' + str(blown_day) + ')' if blown else 'NON'}")

print(f"\n  {'-' * 50}")
print(f"  STATS JOURNALIERES")
print(f"  {'-' * 50}")
print(f"  Jours de trading    : {len(daily_values)}")
print(f"  Jours positifs      : {days_positive}")
print(f"  Jours negatifs      : {days_negative}")
print(f"  Meilleur jour       : ${best_day:+,.0f}")
print(f"  Pire jour           : ${worst_day:+,.0f}")
print(f"  Avg jour            : ${np.mean(daily_values):+,.0f}")

# Projection mensuelle (20 jours de trading)
avg_daily = np.mean(daily_values)
monthly_proj = avg_daily * 20
print(f"\n  Projection mensuelle (20j) : ${monthly_proj:+,.0f}")

# ROI
roi = report.total_pnl_usd / 50000 * 100
print(f"  ROI sur $50K        : {roi:+.1f}%")

print(f"\n  {'-' * 50}")
print(f"  DETAIL PAR JOUR")
print(f"  {'-' * 50}")

cumul = 0.0
running_peak_display = 0.0
print(f"  {'Date':<12}  {'PnL':>10}  {'Cumul':>10}  {'DD':>10}  {'Trades':>7}")
print(f"  {'-' * 55}")

# Count trades per day
trades_per_day = {}
for t in report.trades:
    d = t['date']
    trades_per_day[d] = trades_per_day.get(d, 0) + 1

for d in sorted(daily.keys()):
    pnl = daily[d]
    cumul += pnl
    running_peak_display = max(running_peak_display, cumul)
    dd = cumul - running_peak_display
    n_trades = trades_per_day.get(d, 0)
    dd_marker = " !" if dd <= -TOPSTEP_MAX_DD or pnl <= -TOPSTEP_DAILY_LOSS else ""
    print(f"  {d:<12}  ${pnl:>+9,.0f}  ${cumul:>+9,.0f}  ${dd:>+9,.0f}  {n_trades:>5}{dd_marker}")

print("=" * 70)

# Sauvegarder
import json
from dataclasses import asdict

out = {
    'config': 'MM20 Optimise — MNQ x4 — Topstep $50K',
    'point_value': POINT_VALUE,
    'contracts': '4 MNQ',
    'topstep_max_dd': TOPSTEP_MAX_DD,
    'topstep_daily_loss': TOPSTEP_DAILY_LOSS,
    'compliance': {
        'max_trailing_dd': round(max_trailing_dd, 2),
        'dd_ok': max_trailing_dd < TOPSTEP_MAX_DD,
        'worst_day': round(worst_day, 2),
        'daily_ok': abs(worst_day) < TOPSTEP_DAILY_LOSS,
        'blown': blown,
        'blown_day': str(blown_day) if blown_day else None,
    },
    'report': {
        'total_trades': report.total_trades,
        'win_rate': report.win_rate,
        'total_pnl_usd': report.total_pnl_usd,
        'profit_factor': report.profit_factor,
        'sharpe_ratio': report.sharpe_ratio,
        'max_drawdown_usd': report.max_drawdown_usd,
        'avg_trade': report.avg_trade,
        'avg_win': report.avg_win,
        'avg_loss': report.avg_loss,
        'best_trade': report.best_trade,
        'worst_trade': report.worst_trade,
    },
    'daily_pnl': {k: round(v, 2) for k, v in daily.items()},
    'trades': report.trades,
    'equity_curve': equity,
}

outpath = Path(__file__).resolve().parent.parent / 'data' / 'mm20_topstep50k.json'
outpath.write_text(json.dumps(out, default=str, ensure_ascii=False, indent=2), encoding='utf-8')
print(f"\nSauvegarde: {outpath}")
