"""
BACKTEST MM20 LONG TERME — Yahoo Finance 1h — 2.5 ans
======================================================
Adapte la strategie MM20 au timeframe 1h :
  - Signal : close 1h > SMA20 1h  ET  close daily > SMA20 daily
  - Trailing stop : plus bas/haut des N dernieres bougies 1h
  - TP : +300 pts
  - Sortie temps : 20h39 Paris
  - Debut trades : 16h00 Paris (15h30 + 30min offset)
  - Max 4 trades / jour
  - Daily loss stop : 2 pertes consecutives
  - Daily loss cap : $1000

Compare avec la version 5min sur les 60 jours communs.
"""
import sys
from pathlib import Path
import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backtester.mm20_engine import MM20BacktestEngine
from backtester.opr_engine import is_dst_gap

BASE_DIR = Path(__file__).resolve().parent.parent

# ============================================================
# 1) Charger les donnees Yahoo 1h
# ============================================================
yahoo_path = BASE_DIR / 'data' / 'yahoo_nq_1h.csv'
df_1h = pd.read_csv(yahoo_path, index_col='datetime', parse_dates=True)
if df_1h.index.tz is None:
    df_1h.index = df_1h.index.tz_localize('UTC')

print(f"Yahoo 1h: {len(df_1h)} barres, {df_1h.index.min().date()} -> {df_1h.index.max().date()}")

# ============================================================
# 2) Creer des barres "pseudo-5min" depuis le 1h
#    On ne peut pas — on adapte le moteur pour fonctionner en 1h
#    Mais le moteur attend du 5min. Solution : on fait tourner
#    directement sur les barres 1h en adaptant les parametres.
#    trail_bars=15 en 5min ~= trail_bars=3 en 1h (15*5/60=1.25h -> 2-3 bars)
#    TP reste en points (300 pts)
#    start_offset en 1h : on filtre manuellement
# ============================================================

# On va utiliser le moteur directement sur les barres 1h
# en lui disant que c'est du "5min" — le moteur ne sait pas
# la difference, il itere sur les barres.
# Adaptations necessaires :
#   - trail_bars : 3 (equiv ~3h lookback, similaire a 15 bars x 5min = 75min)
#   - Le resample 1h (pour le filtre trend) : on utilise le daily
#   - SMA20 sur 1h = 20 barres 1h (au lieu de 20 barres 5min)
#   => C'est deja une SMA20 1h naturellement

# Pour le filtre timeframe superieur, on resample en daily
df_daily = df_1h.resample('1D').agg({
    'open': 'first', 'high': 'max', 'low': 'min',
    'close': 'last', 'volume': 'sum'
}).dropna()

print(f"Daily: {len(df_daily)} jours")

# ============================================================
# 3) Run le backtest 1h — MNQ x4 ($8/pt)
# ============================================================
POINT_VALUE = 8.0  # 4 MNQ

# Le moteur prend df_5min et df_1h.
# Ici on passe les barres 1h comme "5min" et le daily comme "1h"
engine = MM20BacktestEngine(
    tp_points=300,
    trail_bars=3,         # 3 barres 1h ~ 3h lookback (equiv 15 bars 5min ~75min)
    max_trades_day=4,
    sma_period=20,
    start_offset_min=30,  # 16h00 Paris (15h30 + 30min)
    min_sma_dist=20,
    atr_min=0,
    daily_loss_stop=2,
    point_value=POINT_VALUE,
    daily_loss_usd=1000,
)

report = engine.run(df_1h, df_1h=df_daily)

if not report:
    print("Aucun trade genere.")
    sys.exit(1)

# ============================================================
# 4) Analyse complete
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

# ============================================================
# 5) Affichage
# ============================================================
print("\n" + "=" * 70)
print("  MM20 LONG TERME - Yahoo 1h - 4 MNQ ($8/pt) - 2.5 ANS")
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
    marker = "+" if m_pnl > 0 else "-"
    if m_pnl > 0:
        months_positive += 1
    else:
        months_negative += 1
    print(f"  {m}  |  {m_trades:>3} trades  |  WR {m_wr:5.1f}%  |  PnL ${m_pnl:>+9,.0f}  {marker}")

print(f"\n  Mois positifs: {months_positive} / {months_positive + months_negative}  ({months_positive/(months_positive+months_negative)*100:.0f}%)")

print("=" * 70)

# Sauvegarder
import json
out = {
    'config': 'MM20 Long Terme - Yahoo 1h - 4 MNQ',
    'source': 'Yahoo Finance 1h',
    'total_trades': report.total_trades,
    'win_rate': report.win_rate,
    'total_pnl_usd': report.total_pnl_usd,
    'profit_factor': report.profit_factor,
    'sharpe_ratio': report.sharpe_ratio,
    'max_drawdown_usd': report.max_drawdown_usd,
    'max_trailing_dd': round(max_trailing_dd, 2),
    'avg_trade': report.avg_trade,
    'avg_win': report.avg_win,
    'avg_loss': report.avg_loss,
    'best_trade': report.best_trade,
    'worst_trade': report.worst_trade,
    'months_positive': months_positive,
    'months_negative': months_negative,
    'daily_pnl': {k: round(v, 2) for k, v in daily.items()},
    'trades': report.trades,
    'equity_curve': equity,
}
outpath = BASE_DIR / 'data' / 'mm20_longterm_yahoo.json'
outpath.write_text(json.dumps(out, default=str, ensure_ascii=False, indent=2), encoding='utf-8')
print(f"\nSauvegarde: {outpath}")
