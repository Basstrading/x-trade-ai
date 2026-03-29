"""
BACKTEST OPR Optimise — Databento NQ 5min — 2.5 ANS
====================================================
Vrais donnees 5min NQ continu depuis Databento.
Config: 4 MNQ ($8/pt), params optimises (dynSL + SAR)
"""
import sys
from pathlib import Path
import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backtester.opr_engine import OPREngine

BASE_DIR = Path(__file__).resolve().parent.parent
POINT_VALUE = 8.0  # 4 MNQ x $2/pt

# ============================================================
# Charger les donnees Databento 5min
# ============================================================
df = pd.read_csv(BASE_DIR / 'data' / 'databento_nq_5min.csv', index_col='datetime', parse_dates=True)
print(f"Databento NQ 5min: {len(df)} barres, {df.index.min().date()} -> {df.index.max().date()}")

# ============================================================
# Run OPR Optimise — params de opr_dynsl_sar_results.json
# adaptes en 4 MNQ ($8/pt)
# ============================================================
params = {
    'sl_type': 'periods_high_low',
    'sl_long_periods': 9,
    'sl_long_delta': -41.75,
    'sl_short_periods': 15,
    'sl_short_delta': 0.25,
    'tp_long': 217.75,
    'tp_short': 205.75,
    'max_trades': 6,
    'max_longs': 3,
    'max_shorts': 3,
    'min_range': 15,
    'max_range': 999,
    'close_hour': 20,
    'close_min': 49,
    'point_value': POINT_VALUE,
    'contracts': 1,
    'sar_enabled': True,
    'auto_dst': True,
    'supertrend_period': 0,
    'daily_loss_limit': -4500,
}

engine = OPREngine(params)
report = engine.run(df, daily_loss_limit=-4500, max_trades_per_day=6)

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

pnls = [t.pnl_dollars for t in report.trades] if hasattr(report.trades[0], 'pnl_dollars') else [t.get('pnl_dollars', 0) for t in report.trades]
wins = [p for p in pnls if p > 0]
losses = [p for p in pnls if p < 0]

# ============================================================
# Affichage
# ============================================================
print("\n" + "=" * 70)
print("  OPR OPTIMISE - DATABENTO 5min - 4 MNQ ($8/pt) - 2.5 ANS")
print("=" * 70)

first_date = report.trades[0].entry_time if hasattr(report.trades[0], 'entry_time') else str(report.trades[0])
last_date = report.trades[-1].entry_time if hasattr(report.trades[-1], 'entry_time') else str(report.trades[-1])
print(f"\n  Periode       : {str(first_date)[:10]} -> {str(last_date)[:10]}")
print(f"  Duree         : {len(daily_values)} jours de trading")
print(f"  Instrument    : MNQ x4 ($8/pt)")

print(f"\n  {'-' * 50}")
print(f"  RESULTATS")
print(f"  {'-' * 50}")
print(f"  Trades        : {report.total_trades}")
print(f"  Win Rate      : {report.win_rate}%")
print(f"  PnL Total     : ${report.total_pnl_dollars:+,.0f}")
print(f"  Profit Factor : {report.profit_factor}")
print(f"  Sharpe Ratio  : {report.sharpe_ratio}")
avg_trade = np.mean(pnls)
print(f"  Avg Trade     : ${avg_trade:+,.0f}")
print(f"  Avg Win       : ${np.mean(wins):+,.0f}" if wins else "  Avg Win       : $0")
print(f"  Avg Loss      : ${np.mean(losses):+,.0f}" if losses else "  Avg Loss      : $0")
print(f"  Best Trade    : ${max(pnls):+,.0f}" if pnls else "  Best Trade    : $0")
print(f"  Worst Trade   : ${min(pnls):+,.0f}" if pnls else "  Worst Trade   : $0")
print(f"  Max Trailing DD : ${max_trailing_dd:,.0f}")

print(f"\n  {'-' * 50}")
print(f"  STATS JOURNALIERES")
print(f"  {'-' * 50}")
print(f"  Jours positifs  : {days_positive}")
print(f"  Jours negatifs  : {days_negative}")
print(f"  Meilleur jour   : ${best_day:+,.0f}")
print(f"  Pire jour       : ${worst_day:+,.0f}")
print(f"  Avg jour        : ${np.mean(daily_values):+,.0f}")

monthly_proj = np.mean(daily_values) * 20
total_months = len(daily_values) / 20
print(f"\n  Projection mensuelle (20j) : ${monthly_proj:+,.0f}")
print(f"  Duree en mois   : {total_months:.1f}")

# Breakdown par trimestre
print(f"\n  {'-' * 50}")
print(f"  BREAKDOWN PAR TRIMESTRE")
print(f"  {'-' * 50}")

# Build trades dataframe
trade_data = []
for t in report.trades:
    if hasattr(t, 'entry_time'):
        entry = str(t.entry_time)[:10]
        pnl_d = t.pnl_dollars
    else:
        entry = str(t.get('entry_time', ''))[:10]
        pnl_d = t.get('pnl_dollars', 0)
    trade_data.append({'date': entry, 'pnl_usd': pnl_d})

trades_df = pd.DataFrame(trade_data)
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
    'config': 'OPR Optimise - Databento 5min - 4 MNQ - 2.5 ans',
    'source': 'Databento NQ.c.0 ohlcv-1m resampled 5min',
    'point_value': POINT_VALUE,
    'total_trades': report.total_trades,
    'win_rate': report.win_rate,
    'total_pnl_dollars': report.total_pnl_dollars,
    'profit_factor': report.profit_factor,
    'sharpe_ratio': report.sharpe_ratio,
    'max_drawdown': report.max_drawdown,
    'max_trailing_dd': round(max_trailing_dd, 2),
    'months_positive': months_positive,
    'months_negative': months_negative,
    'daily_pnl': {str(k): round(v, 2) for k, v in daily.items()},
    'equity_curve': equity,
}
outpath = BASE_DIR / 'data' / 'opr_databento_longterm.json'
outpath.write_text(json.dumps(out, default=str, ensure_ascii=False, indent=2), encoding='utf-8')
print(f"\nSauvegarde: {outpath}")
