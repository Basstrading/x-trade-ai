"""
COMPARAISON : MM20 Pullback SANS ADX vs AVEC ADX
==================================================
On isole l'effet du filtre ADX(14)>22 + pente SMA20 H1
sur la meilleure strategie existante (MM20 Pullback).
Tout le reste est identique.
"""
import sys
from pathlib import Path
import pandas as pd
import numpy as np
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backtester.mm20_engine import MM20BacktestEngine

BASE_DIR = Path(__file__).resolve().parent.parent
POINT_VALUE = 8.0  # 4 MNQ

# ============================================================
# Charger les donnees
# ============================================================
df = pd.read_csv(BASE_DIR / 'data' / 'databento_nq_5min.csv', index_col='datetime', parse_dates=True)
print(f"Donnees: {len(df)} barres 5min, {df.index.min().date()} -> {df.index.max().date()}")

# ============================================================
# Params de base (meilleure config MM20 Pullback)
# ============================================================
BASE = dict(
    tp_points=300,
    trail_bars=15,
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

configs = {
    'MM20 Pullback (SANS ADX)': dict(
        **BASE,
        adx_threshold=0,
        sma_slope_min=0,
    ),
    'MM20 Pullback + ADX>22 + Pente': dict(
        **BASE,
        adx_threshold=22,
        sma_slope_min=0.5,
        sma_slope_bars=3,
    ),
    'MM20 Pullback + ADX>20': dict(
        **BASE,
        adx_threshold=20,
        sma_slope_min=0,
    ),
    'MM20 Pullback + ADX>25': dict(
        **BASE,
        adx_threshold=25,
        sma_slope_min=0,
    ),
    'MM20 Pullback + ADX>22 seul': dict(
        **BASE,
        adx_threshold=22,
        sma_slope_min=0,
    ),
    'MM20 Pullback + Pente seule': dict(
        **BASE,
        adx_threshold=0,
        sma_slope_min=0.5,
        sma_slope_bars=3,
    ),
}

# ============================================================
# Lancer les backtests
# ============================================================
results = {}
for name, params in configs.items():
    t0 = time.time()
    engine = MM20BacktestEngine(**params)
    report = engine.run(df)
    elapsed = time.time() - t0
    if report:
        # Monthly breakdown
        trades_df = pd.DataFrame(report.trades)
        trades_df['date_parsed'] = pd.to_datetime(trades_df['date'])
        trades_df['month'] = trades_df['date_parsed'].dt.to_period('M')
        months_pos = sum(1 for _, g in trades_df.groupby('month') if g['pnl_usd'].sum() > 0)
        months_neg = sum(1 for _, g in trades_df.groupby('month') if g['pnl_usd'].sum() <= 0)

        results[name] = {
            'trades': report.total_trades,
            'wr': report.win_rate,
            'pnl': report.total_pnl_usd,
            'pf': report.profit_factor,
            'sharpe': report.sharpe_ratio,
            'max_dd': report.max_drawdown_usd,
            'avg_trade': report.avg_trade,
            'avg_win': report.avg_win,
            'avg_loss': report.avg_loss,
            'best': report.best_trade,
            'worst': report.worst_trade,
            'months_pos': months_pos,
            'months_neg': months_neg,
            'elapsed': elapsed,
            'report': report,
        }
        print(f"  {name}: {report.total_trades} trades, PF {report.profit_factor:.2f}, PnL ${report.total_pnl_usd:+,.0f} ({elapsed:.1f}s)")
    else:
        print(f"  {name}: AUCUN TRADE")

# ============================================================
# Tableau comparatif
# ============================================================
print(f"\n{'=' * 120}")
print(f"  COMPARATIF : EFFET DU FILTRE ADX SUR MM20 PULLBACK")
print(f"{'=' * 120}")

header = f"  {'Strategie':<35} {'Trades':>7} {'WR':>6} {'PnL':>12} {'PF':>6} {'Sharpe':>7} {'MaxDD':>10} {'AvgTr':>8} {'AvgWin':>8} {'AvgLoss':>8} {'Mois+':>6}"
print(header)
print(f"  {'-' * 115}")

for name, r in results.items():
    total_m = r['months_pos'] + r['months_neg']
    pct = r['months_pos'] / total_m * 100 if total_m > 0 else 0
    print(f"  {name:<35} {r['trades']:>7} {r['wr']:>5.1f}% ${r['pnl']:>+10,.0f} {r['pf']:>5.2f} {r['sharpe']:>6.2f} ${r['max_dd']:>9,.0f} ${r['avg_trade']:>+7,.0f} ${r['avg_win']:>+7,.0f} ${r['avg_loss']:>+7,.0f} {pct:>5.0f}%")

# ============================================================
# Detail mensuel pour les 2 principales (sans ADX vs ADX>22+pente)
# ============================================================
main_configs = ['MM20 Pullback (SANS ADX)', 'MM20 Pullback + ADX>22 + Pente']
for cfg_name in main_configs:
    if cfg_name not in results:
        continue
    r = results[cfg_name]
    trades_df = pd.DataFrame(r['report'].trades)
    trades_df['date_parsed'] = pd.to_datetime(trades_df['date'])
    trades_df['month'] = trades_df['date_parsed'].dt.to_period('M')

    print(f"\n{'=' * 80}")
    print(f"  MOIS : {cfg_name}")
    print(f"{'=' * 80}")
    for m, group in trades_df.groupby('month'):
        m_pnl = group['pnl_usd'].sum()
        m_trades = len(group)
        m_wr = len(group[group['pnl_usd'] > 0]) / m_trades * 100 if m_trades else 0
        marker = "+" if m_pnl > 0 else "!!!"
        print(f"  {m}  |  {m_trades:>3} trades  |  WR {m_wr:5.1f}%  |  PnL ${m_pnl:>+9,.0f}  {marker}")

print(f"\n{'=' * 120}")
