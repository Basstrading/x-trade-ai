"""
RUN H1 ADX + M5 SMA20 PULLBACK BACKTEST — NQ 5min Databento
=============================================================
Strategie:
  H1: close vs SMA20 + ADX(14)>22 + pente SMA20 non plate
  M5: pullback >= 15 pts de la SMA20
  TP: 300 pts | Trail Long: 15 bars | Trail Short: 7 bars
  Sortie temps: 14h39 NY (20h39 Paris)
"""
import sys
from pathlib import Path
import pandas as pd
import numpy as np
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backtester.h1_adx_pullback_engine import H1ADXPullbackEngine

BASE_DIR = Path(__file__).resolve().parent.parent

# ============================================================
# Charger les donnees
# ============================================================
print("=" * 90)
print("  H1 ADX + M5 SMA20 PULLBACK BACKTEST — NQ Nasdaq")
print("=" * 90)

df = pd.read_csv(BASE_DIR / 'data' / 'databento_nq_5min.csv', index_col='datetime', parse_dates=True)
print(f"\n  Donnees: {len(df)} barres 5min, {df.index.min().date()} -> {df.index.max().date()}")

# ============================================================
# Lancer le backtest avec les parametres exacts
# ============================================================
POINT_VALUE = 20.0  # NQ: $20/pt

t0 = time.time()

engine = H1ADXPullbackEngine(
    tp_points=300,
    trail_bars_long=15,
    trail_bars_short=7,
    sma_period=20,
    adx_period=14,
    adx_threshold=22,
    pullback_distance=15,
    sma_slope_bars=3,
    sma_slope_min=0.5,
    point_value=POINT_VALUE,
    max_trades_day=99,  # pas de limite mentionnee
)

report = engine.run(df)
elapsed = time.time() - t0
print(f"  Temps d'execution: {elapsed:.1f}s\n")

if report is None:
    print("  ERREUR: Aucun trade genere!")
    sys.exit(1)

# ============================================================
# Resultats principaux
# ============================================================
print("=" * 90)
print("  RESULTATS PRINCIPAUX")
print("=" * 90)
print(f"  Trades totaux      : {report.total_trades}")
print(f"  Trades gagnants    : {report.winning_trades}")
print(f"  Trades perdants    : {report.losing_trades}")
print(f"  Win Rate           : {report.win_rate:.1f}%")
print(f"  PnL Total          : ${report.total_pnl_usd:>+,.0f}")
print(f"  Avg Trade          : ${report.avg_trade:>+,.0f}")
print(f"  Avg Win            : ${report.avg_win:>+,.0f}")
print(f"  Avg Loss           : ${report.avg_loss:>+,.0f}")
print(f"  Profit Factor      : {report.profit_factor:.2f}")
print(f"  Sharpe Ratio       : {report.sharpe_ratio:.2f}")
print(f"  Max Drawdown       : ${report.max_drawdown_usd:>,.0f}")
print(f"  Best Trade         : ${report.best_trade:>+,.0f}")
print(f"  Worst Trade        : ${report.worst_trade:>+,.0f}")

# Ratio TP vs SL
tp_trades = [t for t in report.trades if t['exit_reason'] == 'tp']
sl_trades = [t for t in report.trades if t['exit_reason'] == 'trail_stop']
time_trades = [t for t in report.trades if t['exit_reason'] == 'time']
eod_trades = [t for t in report.trades if t['exit_reason'] == 'eod']

print(f"\n  --- Sorties ---")
print(f"  Take Profit        : {len(tp_trades)} ({len(tp_trades)/report.total_trades*100:.1f}%)")
print(f"  Trailing Stop      : {len(sl_trades)} ({len(sl_trades)/report.total_trades*100:.1f}%)")
print(f"  Time (14h39 NY)    : {len(time_trades)} ({len(time_trades)/report.total_trades*100:.1f}%)")
print(f"  EOD                : {len(eod_trades)} ({len(eod_trades)/report.total_trades*100:.1f}%)")

# Direction breakdown
long_trades = [t for t in report.trades if t['direction'] == 'long']
short_trades = [t for t in report.trades if t['direction'] == 'short']
long_pnl = sum(t['pnl_usd'] for t in long_trades)
short_pnl = sum(t['pnl_usd'] for t in short_trades)
long_wr = len([t for t in long_trades if t['pnl_usd'] > 0]) / len(long_trades) * 100 if long_trades else 0
short_wr = len([t for t in short_trades if t['pnl_usd'] > 0]) / len(short_trades) * 100 if short_trades else 0

print(f"\n  --- Par direction ---")
print(f"  LONG  : {len(long_trades):>4} trades | WR {long_wr:5.1f}% | PnL ${long_pnl:>+10,.0f}")
print(f"  SHORT : {len(short_trades):>4} trades | WR {short_wr:5.1f}% | PnL ${short_pnl:>+10,.0f}")

# ============================================================
# Breakdown par trimestre
# ============================================================
trades_df = pd.DataFrame(report.trades)
trades_df['date_parsed'] = pd.to_datetime(trades_df['date'])
trades_df['quarter'] = trades_df['date_parsed'].dt.to_period('Q')
trades_df['month'] = trades_df['date_parsed'].dt.to_period('M')

print(f"\n{'=' * 90}")
print(f"  BREAKDOWN PAR TRIMESTRE")
print(f"{'=' * 90}")
for q, group in trades_df.groupby('quarter'):
    q_pnls = group['pnl_usd'].tolist()
    q_wins = [p for p in q_pnls if p > 0]
    q_losses = [p for p in q_pnls if p < 0]
    q_wr = len(q_wins) / len(q_pnls) * 100 if q_pnls else 0
    q_pnl = sum(q_pnls)
    q_pf = abs(sum(q_wins) / sum(q_losses)) if q_losses and sum(q_losses) != 0 else 99
    marker = "+" if q_pnl > 0 else "!!!"
    print(f"  {q}  |  {len(q_pnls):>4} trades  |  WR {q_wr:5.1f}%  |  PnL ${q_pnl:>+10,.0f}  |  PF {q_pf:.2f}  {marker}")

# ============================================================
# Breakdown par mois
# ============================================================
print(f"\n{'=' * 90}")
print(f"  BREAKDOWN PAR MOIS")
print(f"{'=' * 90}")
months_pos = 0
months_neg = 0
for m, group in trades_df.groupby('month'):
    m_pnl = group['pnl_usd'].sum()
    m_trades = len(group)
    m_wr = len(group[group['pnl_usd'] > 0]) / m_trades * 100 if m_trades else 0
    marker = "+" if m_pnl > 0 else "-"
    if m_pnl > 0:
        months_pos += 1
    else:
        months_neg += 1
    print(f"  {m}  |  {m_trades:>3} trades  |  WR {m_wr:5.1f}%  |  PnL ${m_pnl:>+9,.0f}  {marker}")

total_months = months_pos + months_neg
print(f"\n  Mois positifs: {months_pos}/{total_months} ({months_pos/total_months*100:.0f}%)")
print(f"  Mois negatifs: {months_neg}/{total_months} ({months_neg/total_months*100:.0f}%)")

# ============================================================
# 20 derniers trades
# ============================================================
print(f"\n{'=' * 90}")
print(f"  20 DERNIERS TRADES")
print(f"{'=' * 90}")
print(f"  {'Date':<12} {'Dir':<6} {'Entry':>10} {'Exit':>10} {'PnL pts':>9} {'PnL $':>9} {'Raison':<12}")
print(f"  {'-' * 75}")
for t in report.trades[-20:]:
    print(f"  {t['date']:<12} {t['direction']:<6} {t['entry']:>10.2f} {t['exit']:>10.2f} {t['pnl_pts']:>+9.2f} ${t['pnl_usd']:>+8,.0f} {t['exit_reason']:<12}")

print(f"\n{'=' * 90}")

# ============================================================
# Sauvegarder
# ============================================================
import json

out = {
    'strategy': 'H1_ADX_M5_SMA20_Pullback',
    'params': {
        'tp_points': 300,
        'trail_bars_long': 15,
        'trail_bars_short': 7,
        'sma_period': 20,
        'adx_period': 14,
        'adx_threshold': 22,
        'pullback_distance': 15,
        'sma_slope_bars': 3,
        'point_value': POINT_VALUE,
    },
    'results': {
        'total_trades': report.total_trades,
        'winning_trades': report.winning_trades,
        'losing_trades': report.losing_trades,
        'win_rate': report.win_rate,
        'total_pnl_usd': report.total_pnl_usd,
        'avg_trade': report.avg_trade,
        'avg_win': report.avg_win,
        'avg_loss': report.avg_loss,
        'profit_factor': report.profit_factor,
        'sharpe_ratio': report.sharpe_ratio,
        'max_drawdown_usd': report.max_drawdown_usd,
        'best_trade': report.best_trade,
        'worst_trade': report.worst_trade,
        'months_positive': months_pos,
        'months_negative': months_neg,
    },
    'trades': report.trades,
}

outpath = BASE_DIR / 'data' / 'h1_adx_pullback_results.json'
outpath.write_text(json.dumps(out, default=str, ensure_ascii=False, indent=2), encoding='utf-8')
print(f"  Resultats sauvegardes: {outpath}")
