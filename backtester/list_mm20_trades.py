"""Liste tous les trades MM20 optimise."""
import sys
from pathlib import Path
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backtester.mm20_engine import MM20BacktestEngine

df = pd.read_csv('data/cache_5min.csv', index_col='datetime', parse_dates=True)
engine = MM20BacktestEngine(
    tp_points=300, trail_bars=15, max_trades_day=4, sma_period=20,
    start_offset_min=30, min_sma_dist=20, atr_min=0, daily_loss_stop=2,
)
report = engine.run(df)

print(f"Total: {report.total_trades} trades | WR {report.win_rate}% | PnL ${report.total_pnl_usd:+,.0f}\n")

# Header
print(f"{'#':>3}  {'Date':<12}  {'Entree':<14}  {'Sortie':<14}  {'Dir':<5}  {'Prix E':>9}  {'Prix S':>9}  {'Stop':>9}  {'PnL pts':>9}  {'PnL $':>10}  {'Exit':<12}")
print("-" * 130)

cumul = 0.0
for i, t in enumerate(report.trades):
    # Parse times
    et = t['entry_time']
    xt = t['exit_time']
    # Extract HH:MM from Paris time string like "2026-01-14 16:00:00+01:00"
    e_hm = et.split(' ')[-1][:5] if ' ' in et else et[:5]
    x_hm = xt.split(' ')[-1][:5] if ' ' in xt else xt[:5]

    d = t['direction'][0].upper()
    cumul += t['pnl_usd']

    pnl_str = f"${t['pnl_usd']:+,.0f}"

    print(f"{i+1:>3}  {t['date']:<12}  {e_hm:<14}  {x_hm:<14}  {d:<5}  {t['entry']:>9.2f}  {t['exit']:>9.2f}  {t['stop']:>9.2f}  {t['pnl_pts']:>+9.2f}  {pnl_str:>10}  {t['exit_reason']:<12}")

print("-" * 130)
print(f"{'':>3}  {'':>12}  {'':>14}  {'':>14}  {'':>5}  {'':>9}  {'':>9}  {'':>9}  {'':>9}  {'$'+str(int(cumul)):>10}  CUMUL")
