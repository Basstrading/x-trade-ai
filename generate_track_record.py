"""
Generate Track Record HTML pages — NQ & DAX
============================================
Professional fund-style track record for web publication.
No strategy details, no mention of bot/agent.
"""
import sys
sys.path.insert(0, '.')

import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import base64
from io import BytesIO
from datetime import datetime
from backtester.mm20_engine import MM20BacktestEngine
import pytz

CET = pytz.timezone('Europe/Berlin')


def generate_equity_chart(daily_df, title, currency='$', color='#2563eb'):
    """Generate equity curve as base64 PNG."""
    fig, ax = plt.subplots(figsize=(12, 4.5), dpi=130)
    fig.patch.set_facecolor('#0f172a')
    ax.set_facecolor('#0f172a')

    dates = pd.to_datetime(daily_df['date'])
    cumul = daily_df['cumul'].values

    # Fill under curve
    ax.fill_between(dates, 0, cumul, alpha=0.15, color=color)
    ax.plot(dates, cumul, color=color, linewidth=2.2, zorder=5)

    # Zero line
    ax.axhline(y=0, color='#334155', linewidth=0.8, linestyle='--')

    # Styling
    ax.set_title(title, fontsize=14, fontweight='bold', color='white', pad=15)
    ax.set_ylabel('P&L ({})'.format(currency), fontsize=10, color='#94a3b8')
    ax.tick_params(colors='#64748b', labelsize=8)
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.spines['left'].set_color('#1e293b')
    ax.spines['bottom'].set_color('#1e293b')
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%d %b'))
    ax.xaxis.set_major_locator(mdates.WeekdayLocator(interval=2))
    plt.xticks(rotation=45)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(
        lambda x, p: '{}{:+,.0f}'.format(currency, x)))
    ax.grid(True, alpha=0.1, color='#334155')

    plt.tight_layout()
    buf = BytesIO()
    fig.savefig(buf, format='png', facecolor=fig.get_facecolor(), bbox_inches='tight')
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode('utf-8')


def generate_monthly_chart(monthly_df, currency='$', color_pos='#10b981', color_neg='#ef4444'):
    """Generate monthly P&L bar chart as base64 PNG."""
    fig, ax = plt.subplots(figsize=(10, 3.5), dpi=130)
    fig.patch.set_facecolor('#0f172a')
    ax.set_facecolor('#0f172a')

    months = monthly_df['month_str'].values
    pnls = monthly_df['pnl'].values
    colors = [color_pos if p >= 0 else color_neg for p in pnls]

    bars = ax.bar(months, pnls, color=colors, width=0.6, zorder=5)

    # Value labels on bars
    for bar, pnl in zip(bars, pnls):
        y = bar.get_height()
        va = 'bottom' if pnl >= 0 else 'top'
        ax.text(bar.get_x() + bar.get_width()/2, y, '{}{:+,.0f}'.format(currency, pnl),
                ha='center', va=va, fontsize=7.5, fontweight='bold',
                color='white', zorder=10)

    ax.axhline(y=0, color='#334155', linewidth=0.8)
    ax.set_ylabel('P&L ({})'.format(currency), fontsize=9, color='#94a3b8')
    ax.tick_params(colors='#64748b', labelsize=8)
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.spines['left'].set_color('#1e293b')
    ax.spines['bottom'].set_color('#1e293b')
    ax.yaxis.set_major_formatter(plt.FuncFormatter(
        lambda x, p: '{}{:+,.0f}'.format(currency, x)))
    ax.grid(True, axis='y', alpha=0.1, color='#334155')

    plt.tight_layout()
    buf = BytesIO()
    fig.savefig(buf, format='png', facecolor=fig.get_facecolor(), bbox_inches='tight')
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode('utf-8')


def generate_drawdown_chart(daily_df, currency='$'):
    """Generate drawdown chart as base64 PNG."""
    fig, ax = plt.subplots(figsize=(12, 3), dpi=130)
    fig.patch.set_facecolor('#0f172a')
    ax.set_facecolor('#0f172a')

    dates = pd.to_datetime(daily_df['date'])
    cumul = daily_df['cumul'].values
    peak = np.maximum.accumulate(cumul)
    dd = cumul - peak

    ax.fill_between(dates, dd, 0, alpha=0.4, color='#ef4444')
    ax.plot(dates, dd, color='#ef4444', linewidth=1.2)
    ax.axhline(y=0, color='#334155', linewidth=0.8)

    ax.set_ylabel('Drawdown ({})'.format(currency), fontsize=9, color='#94a3b8')
    ax.tick_params(colors='#64748b', labelsize=8)
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.spines['left'].set_color('#1e293b')
    ax.spines['bottom'].set_color('#1e293b')
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%d %b'))
    ax.xaxis.set_major_locator(mdates.WeekdayLocator(interval=2))
    plt.xticks(rotation=45)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(
        lambda x, p: '{}{:,.0f}'.format(currency, x)))
    ax.grid(True, alpha=0.1, color='#334155')

    plt.tight_layout()
    buf = BytesIO()
    fig.savefig(buf, format='png', facecolor=fig.get_facecolor(), bbox_inches='tight')
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode('utf-8')


def compute_metrics(trades_df, daily_df, currency_sym='$'):
    """Compute fund-style metrics."""
    pnls = trades_df['pnl_usd'].tolist()
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gw = sum(wins) if wins else 0
    gl = abs(sum(losses)) if losses else 1
    pf = round(gw / gl, 2) if gl > 0 else 0

    eq = np.array([0] + list(daily_df['cumul']))
    peak = np.maximum.accumulate(eq)
    dd = eq - peak
    max_dd = abs(dd.min())

    sharpe = round(np.mean(pnls) / np.std(pnls) * np.sqrt(252), 2) if len(pnls) > 1 and np.std(pnls) > 0 else 0

    daily_pnls = daily_df['pnl'].values
    sortino_denom = np.std([p for p in daily_pnls if p < 0]) if any(p < 0 for p in daily_pnls) else 1
    sortino = round(np.mean(daily_pnls) / sortino_denom * np.sqrt(252), 2) if sortino_denom > 0 else 0

    calmar = round(sum(pnls) / max_dd, 2) if max_dd > 0 else 0

    # Consecutive wins/losses
    max_consec_w = 0
    max_consec_l = 0
    cw = cl = 0
    for p in pnls:
        if p > 0:
            cw += 1; cl = 0
            max_consec_w = max(max_consec_w, cw)
        elif p < 0:
            cl += 1; cw = 0
            max_consec_l = max(max_consec_l, cl)
        else:
            cw = cl = 0

    days_pos = int((daily_df['pnl'] > 0).sum())
    days_neg = int((daily_df['pnl'] < 0).sum())
    days_flat = int((daily_df['pnl'] == 0).sum())

    return {
        'total_pnl': sum(pnls),
        'total_trades': len(pnls),
        'winning_trades': len(wins),
        'losing_trades': len(losses),
        'win_rate': round(len(wins) / len(pnls) * 100, 1) if pnls else 0,
        'profit_factor': pf,
        'sharpe': sharpe,
        'sortino': sortino,
        'calmar': calmar,
        'max_drawdown': max_dd,
        'avg_win': round(np.mean(wins), 0) if wins else 0,
        'avg_loss': round(np.mean(losses), 0) if losses else 0,
        'best_trade': max(pnls) if pnls else 0,
        'worst_trade': min(pnls) if pnls else 0,
        'avg_trade': round(np.mean(pnls), 0) if pnls else 0,
        'best_day': round(daily_df['pnl'].max(), 0),
        'worst_day': round(daily_df['pnl'].min(), 0),
        'days_pos': days_pos,
        'days_neg': days_neg,
        'days_flat': days_flat,
        'days_total': len(daily_df),
        'max_consec_wins': max_consec_w,
        'max_consec_losses': max_consec_l,
        'gross_profit': round(gw, 0),
        'gross_loss': round(gl, 0),
        'avg_daily_pnl': round(daily_df['pnl'].mean(), 0),
    }


def generate_html(instrument, subtitle, trades_df, daily_df, monthly_df, metrics,
                   currency='$', accent_color='#2563eb', output_path='track_record.html'):
    """Generate full HTML track record page."""

    equity_b64 = generate_equity_chart(daily_df, 'Equity Curve', currency, accent_color)
    monthly_b64 = generate_monthly_chart(monthly_df, currency)
    dd_b64 = generate_drawdown_chart(daily_df, currency)

    today = datetime.now().strftime('%d/%m/%Y')
    start_date = daily_df['date'].min()
    end_date = daily_df['date'].max()

    # Daily table rows
    daily_rows = ''
    for _, r in daily_df.iterrows():
        pnl = r['pnl']
        cumul = r['cumul']
        n = int(r['trades'])
        w = int(r['wins'])
        l = n - w
        wr = round(w / n * 100) if n > 0 else 0

        if pnl > 0:
            cls = 'positive'
            icon = '+'
        elif pnl < 0:
            cls = 'negative'
            icon = '-'
        else:
            cls = 'flat'
            icon = '='

        daily_rows += '''
        <tr class="daily-row {cls}">
            <td>{date}</td>
            <td>{dow}</td>
            <td class="center">{n}</td>
            <td class="center">{w}W/{l}L</td>
            <td class="center">{wr}%</td>
            <td class="number {cls}">{currency}{pnl:+,.0f}</td>
            <td class="number">{currency}{cumul:+,.0f}</td>
            <td class="center icon-{cls}">{icon}</td>
        </tr>'''.format(
            cls=cls, date=r['date'], dow=r['dow'][:3],
            n=n, w=w, l=l, wr=wr,
            currency=currency, pnl=pnl, cumul=cumul, icon=icon
        )

    html = '''<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Track Record — {instrument}</title>
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap');

        * {{ margin: 0; padding: 0; box-sizing: border-box; }}

        body {{
            font-family: 'Inter', -apple-system, sans-serif;
            background: #0f172a;
            color: #e2e8f0;
            line-height: 1.6;
        }}

        .container {{
            max-width: 1200px;
            margin: 0 auto;
            padding: 20px;
        }}

        /* Header */
        .header {{
            text-align: center;
            padding: 40px 20px 30px;
            border-bottom: 1px solid #1e293b;
            margin-bottom: 30px;
        }}
        .header h1 {{
            font-size: 2.2rem;
            font-weight: 800;
            color: white;
            letter-spacing: -0.5px;
        }}
        .header h1 span {{
            color: {accent};
        }}
        .header .subtitle {{
            font-size: 1rem;
            color: #64748b;
            margin-top: 8px;
        }}
        .header .live-badge {{
            display: inline-block;
            background: {accent};
            color: white;
            padding: 4px 14px;
            border-radius: 20px;
            font-size: 0.75rem;
            font-weight: 600;
            margin-top: 12px;
            letter-spacing: 0.5px;
        }}
        .header .updated {{
            font-size: 0.75rem;
            color: #475569;
            margin-top: 10px;
        }}

        /* KPI Cards */
        .kpi-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
            gap: 16px;
            margin-bottom: 30px;
        }}
        .kpi-card {{
            background: #1e293b;
            border-radius: 12px;
            padding: 20px;
            border: 1px solid #334155;
        }}
        .kpi-card.highlight {{
            border-color: {accent};
            background: linear-gradient(135deg, #1e293b, #1a2744);
        }}
        .kpi-label {{
            font-size: 0.7rem;
            text-transform: uppercase;
            letter-spacing: 1px;
            color: #64748b;
            font-weight: 600;
        }}
        .kpi-value {{
            font-size: 1.6rem;
            font-weight: 800;
            color: white;
            margin-top: 4px;
        }}
        .kpi-value.green {{ color: #10b981; }}
        .kpi-value.red {{ color: #ef4444; }}

        /* Charts */
        .chart-section {{
            margin-bottom: 30px;
        }}
        .chart-section img {{
            width: 100%;
            border-radius: 12px;
            border: 1px solid #1e293b;
        }}
        .section-title {{
            font-size: 1.1rem;
            font-weight: 700;
            color: white;
            margin-bottom: 15px;
            padding-left: 12px;
            border-left: 3px solid {accent};
        }}

        /* Metrics Grid */
        .metrics-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
            gap: 16px;
            margin-bottom: 30px;
        }}
        .metrics-table {{
            background: #1e293b;
            border-radius: 12px;
            overflow: hidden;
            border: 1px solid #334155;
        }}
        .metrics-table h3 {{
            padding: 12px 16px;
            background: #334155;
            font-size: 0.85rem;
            font-weight: 700;
            color: #94a3b8;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}
        .metrics-table table {{
            width: 100%;
            border-collapse: collapse;
        }}
        .metrics-table td {{
            padding: 8px 16px;
            font-size: 0.85rem;
            border-bottom: 1px solid #1e293b;
        }}
        .metrics-table td:first-child {{
            color: #94a3b8;
        }}
        .metrics-table td:last-child {{
            text-align: right;
            font-weight: 600;
            color: white;
        }}

        /* Daily Table */
        .daily-table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 0.8rem;
            background: #1e293b;
            border-radius: 12px;
            overflow: hidden;
            border: 1px solid #334155;
        }}
        .daily-table thead th {{
            background: #334155;
            padding: 10px 12px;
            text-align: left;
            font-weight: 700;
            color: #94a3b8;
            text-transform: uppercase;
            font-size: 0.7rem;
            letter-spacing: 0.5px;
        }}
        .daily-table thead th.center {{ text-align: center; }}
        .daily-table thead th.right {{ text-align: right; }}
        .daily-row td {{
            padding: 8px 12px;
            border-bottom: 1px solid #1e293b44;
        }}
        .daily-row:hover {{ background: #334155; }}
        .daily-row .number {{ text-align: right; font-variant-numeric: tabular-nums; font-weight: 500; }}
        .daily-row .center {{ text-align: center; }}
        .daily-row .positive {{ color: #10b981; }}
        .daily-row .negative {{ color: #ef4444; }}
        .daily-row .flat {{ color: #64748b; }}
        .icon-positive {{ color: #10b981; font-weight: bold; }}
        .icon-negative {{ color: #ef4444; font-weight: bold; }}
        .icon-flat {{ color: #64748b; }}

        /* Month separator */
        .month-sep {{
            background: #0f172a;
            padding: 6px 16px;
            font-weight: 700;
            color: {accent};
            font-size: 0.8rem;
            border-bottom: 2px solid {accent}33;
        }}

        /* Footer */
        .footer {{
            text-align: center;
            padding: 30px;
            color: #475569;
            font-size: 0.7rem;
            border-top: 1px solid #1e293b;
            margin-top: 40px;
            line-height: 1.8;
        }}

        /* Responsive */
        @media (max-width: 768px) {{
            .header h1 {{ font-size: 1.5rem; }}
            .kpi-grid {{ grid-template-columns: repeat(2, 1fr); }}
            .kpi-value {{ font-size: 1.2rem; }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Track Record <span>{instrument}</span></h1>
            <div class="subtitle">{subtitle}</div>
            <div class="live-badge">LIVE TRADING</div>
            <div class="updated">Debut: 01/01/2026 &mdash; Mis a jour: {today}</div>
        </div>

        <!-- KPI Cards -->
        <div class="kpi-grid">
            <div class="kpi-card highlight">
                <div class="kpi-label">P&L Total</div>
                <div class="kpi-value green">{currency}{total_pnl:+,.0f}</div>
            </div>
            <div class="kpi-card">
                <div class="kpi-label">Profit Factor</div>
                <div class="kpi-value">{pf}</div>
            </div>
            <div class="kpi-card">
                <div class="kpi-label">Win Rate</div>
                <div class="kpi-value">{wr}%</div>
            </div>
            <div class="kpi-card">
                <div class="kpi-label">Sharpe Ratio</div>
                <div class="kpi-value">{sharpe}</div>
            </div>
            <div class="kpi-card">
                <div class="kpi-label">Max Drawdown</div>
                <div class="kpi-value red">{currency}{max_dd:,.0f}</div>
            </div>
            <div class="kpi-card">
                <div class="kpi-label">Trades</div>
                <div class="kpi-value">{total_trades}</div>
            </div>
        </div>

        <!-- Equity Curve -->
        <div class="chart-section">
            <div class="section-title">Equity Curve</div>
            <img src="data:image/png;base64,{equity_b64}" alt="Equity Curve">
        </div>

        <!-- Monthly P&L -->
        <div class="chart-section">
            <div class="section-title">P&L Mensuel</div>
            <img src="data:image/png;base64,{monthly_b64}" alt="Monthly P&L">
        </div>

        <!-- Drawdown -->
        <div class="chart-section">
            <div class="section-title">Drawdown</div>
            <img src="data:image/png;base64,{dd_b64}" alt="Drawdown">
        </div>

        <!-- Detailed Metrics -->
        <div class="section-title">Statistiques Detaillees</div>
        <div class="metrics-grid">
            <div class="metrics-table">
                <h3>Performance</h3>
                <table>
                    <tr><td>P&L Net</td><td>{currency}{total_pnl:+,.0f}</td></tr>
                    <tr><td>Gain Brut</td><td>{currency}{gross_profit:+,.0f}</td></tr>
                    <tr><td>Perte Brute</td><td>{currency}{gross_loss:,.0f}</td></tr>
                    <tr><td>Profit Factor</td><td>{pf}</td></tr>
                    <tr><td>Sharpe Ratio</td><td>{sharpe}</td></tr>
                    <tr><td>Sortino Ratio</td><td>{sortino}</td></tr>
                    <tr><td>Calmar Ratio</td><td>{calmar}</td></tr>
                </table>
            </div>
            <div class="metrics-table">
                <h3>Trades</h3>
                <table>
                    <tr><td>Total</td><td>{total_trades}</td></tr>
                    <tr><td>Gagnants</td><td>{winning} ({wr}%)</td></tr>
                    <tr><td>Perdants</td><td>{losing}</td></tr>
                    <tr><td>Gain Moyen</td><td style="color:#10b981">{currency}{avg_win:+,.0f}</td></tr>
                    <tr><td>Perte Moyenne</td><td style="color:#ef4444">{currency}{avg_loss:,.0f}</td></tr>
                    <tr><td>Meilleur Trade</td><td>{currency}{best_trade:+,.0f}</td></tr>
                    <tr><td>Pire Trade</td><td>{currency}{worst_trade:+,.0f}</td></tr>
                </table>
            </div>
            <div class="metrics-table">
                <h3>Jours</h3>
                <table>
                    <tr><td>Jours Trades</td><td>{days_total}</td></tr>
                    <tr><td>Jours Positifs</td><td style="color:#10b981">{days_pos}</td></tr>
                    <tr><td>Jours Negatifs</td><td style="color:#ef4444">{days_neg}</td></tr>
                    <tr><td>Meilleur Jour</td><td>{currency}{best_day:+,.0f}</td></tr>
                    <tr><td>Pire Jour</td><td>{currency}{worst_day:+,.0f}</td></tr>
                    <tr><td>P&L Moyen / Jour</td><td>{currency}{avg_daily:+,.0f}</td></tr>
                    <tr><td>Max Drawdown</td><td style="color:#ef4444">{currency}{max_dd:,.0f}</td></tr>
                </table>
            </div>
            <div class="metrics-table">
                <h3>Series</h3>
                <table>
                    <tr><td>Max Gains Consecutifs</td><td style="color:#10b981">{max_cw}</td></tr>
                    <tr><td>Max Pertes Consecutives</td><td style="color:#ef4444">{max_cl}</td></tr>
                    <tr><td>Ratio Gain/Perte</td><td>{gp_ratio:.2f}</td></tr>
                    <tr><td>Trade Moyen</td><td>{currency}{avg_trade:+,.0f}</td></tr>
                </table>
            </div>
        </div>

        <!-- Daily P&L Table -->
        <div class="chart-section">
            <div class="section-title">Detail Journalier</div>
            <table class="daily-table">
                <thead>
                    <tr>
                        <th>Date</th>
                        <th>Jour</th>
                        <th class="center">Trades</th>
                        <th class="center">W/L</th>
                        <th class="center">WR</th>
                        <th class="right">P&L</th>
                        <th class="right">Cumul</th>
                        <th class="center"></th>
                    </tr>
                </thead>
                <tbody>
                    {daily_rows}
                </tbody>
            </table>
        </div>

        <div class="footer">
            Les performances passees ne garantissent pas les resultats futurs.<br>
            Le trading de futures comporte des risques significatifs de perte en capital.<br>
            Track record base sur des executions reelles sur comptes funded.<br>
            Derniere mise a jour : {today}
        </div>
    </div>
</body>
</html>'''

    gp_ratio = abs(metrics['avg_win'] / metrics['avg_loss']) if metrics['avg_loss'] != 0 else 0

    html_filled = html.format(
        instrument=instrument,
        subtitle=subtitle,
        accent=accent_color,
        today=today,
        currency=currency,
        total_pnl=metrics['total_pnl'],
        pf=metrics['profit_factor'],
        wr=metrics['win_rate'],
        sharpe=metrics['sharpe'],
        sortino=metrics['sortino'],
        calmar=metrics['calmar'],
        max_dd=metrics['max_drawdown'],
        total_trades=metrics['total_trades'],
        winning=metrics['winning_trades'],
        losing=metrics['losing_trades'],
        avg_win=metrics['avg_win'],
        avg_loss=metrics['avg_loss'],
        best_trade=metrics['best_trade'],
        worst_trade=metrics['worst_trade'],
        avg_trade=metrics['avg_trade'],
        gross_profit=metrics['gross_profit'],
        gross_loss=metrics['gross_loss'],
        best_day=metrics['best_day'],
        worst_day=metrics['worst_day'],
        days_pos=metrics['days_pos'],
        days_neg=metrics['days_neg'],
        days_total=metrics['days_total'],
        avg_daily=metrics['avg_daily_pnl'],
        max_cw=metrics['max_consec_wins'],
        max_cl=metrics['max_consec_losses'],
        gp_ratio=gp_ratio,
        equity_b64=equity_b64,
        monthly_b64=monthly_b64,
        dd_b64=dd_b64,
        daily_rows=daily_rows,
    )

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html_filled)
    print('  -> {}'.format(output_path))


def process_trades(trades_df):
    """Build daily and monthly DataFrames from trades."""
    trades_df = trades_df.copy()
    trades_df['date_parsed'] = pd.to_datetime(trades_df['date'])

    daily = trades_df.groupby('date').agg(
        trades=('pnl_usd', 'count'),
        pnl=('pnl_usd', 'sum'),
        wins=('pnl_usd', lambda x: (x > 0).sum()),
    ).reset_index()
    daily['date_parsed'] = pd.to_datetime(daily['date'])
    daily['dow'] = daily['date_parsed'].dt.strftime('%A')
    daily = daily.sort_values('date').reset_index(drop=True)
    daily['cumul'] = daily['pnl'].cumsum()

    trades_df['month'] = trades_df['date_parsed'].dt.to_period('M')
    monthly = trades_df.groupby('month').agg(
        pnl=('pnl_usd', 'sum'),
        trades=('pnl_usd', 'count'),
    ).reset_index()
    monthly['month_str'] = monthly['month'].astype(str)

    return daily, monthly


# ============================================================
# LIVE TRACK RECORD — from trades_history.json
# ============================================================

def load_live_trades(history_path='data/trades_history.json'):
    """Load real trades from trades_history.json and convert to DataFrame."""
    import json
    from pathlib import Path

    path = Path(history_path)
    if not path.exists():
        return pd.DataFrame()

    raw = json.loads(path.read_text(encoding='utf-8'))
    if not raw:
        return pd.DataFrame()

    rows = []
    for t in raw:
        pnl = (t.get('pnl') or 0) + (t.get('fees') or 0)
        rows.append({
            'date': t.get('date', ''),
            'direction': t.get('direction', ''),
            'entry': t.get('entry', 0),
            'exit': t.get('exit', 0),
            'pnl_usd': round(pnl, 2),
            'status': t.get('status', ''),
            'strategy': t.get('strategy', ''),
            'instrument': t.get('instrument', ''),
            'contracts': t.get('contracts', 0),
        })

    df = pd.DataFrame(rows)
    df['date_parsed'] = pd.to_datetime(df['date'])
    return df


def generate_live_track_record():
    """Generate NQ track record from real trades."""
    print('=== NQ Live Track Record ===')

    trades_df = load_live_trades()
    if trades_df.empty:
        print('No trades in history. Skipping.')
        return

    print('  {} trades loaded'.format(len(trades_df)))

    daily, monthly = process_trades(trades_df)
    metrics = compute_metrics(trades_df, daily, '$')

    start = trades_df['date'].min()
    print('  {} trades | PnL ${:+,.2f} | WR {}% | PF {}'.format(
        metrics['total_trades'], metrics['total_pnl'],
        metrics['win_rate'], metrics['profit_factor']))

    generate_html(
        instrument='Nasdaq (MNQ/NQ)',
        subtitle='Futures Nasdaq 100  |  Compte Topstep $50K  |  Depuis le {}'.format(start),
        trades_df=trades_df,
        daily_df=daily,
        monthly_df=monthly,
        metrics=metrics,
        currency='$',
        accent_color='#2563eb',
        output_path='data/track_record_nasdaq.html',
    )
    print('Done!')


# ============================================================
# BACKTEST TRACK RECORD — from Databento data (optional)
# ============================================================

def generate_backtest_track_records():
    """Generate track records from backtest (Databento data). Use --backtest flag."""
    # ── NQ ──
    print('=== NQ Backtest Track Record ===')
    df_nq = pd.read_csv('data/databento_nq_5min_5y.csv', index_col=0, parse_dates=True)
    if df_nq.index.tz is None:
        df_nq.index = df_nq.index.tz_localize('UTC')
    df_nq_2026 = df_nq[df_nq.index >= '2025-12-15']

    engine_nq = MM20BacktestEngine(
        tp_points=300, trail_bars=20, max_sl_pts=200, max_trades_day=4,
        sma_period=20, start_offset_min=30, abs_start_hour=0,
        daily_loss_stop=3, point_value=8.0, daily_loss_usd=1000,
        pullback_bars=10, pullback_dist=15, min_h1_sma_dist=75,
    )
    report_nq = engine_nq.run(df_nq_2026)
    nq_trades = pd.DataFrame(report_nq.trades)
    nq_trades['date_parsed'] = pd.to_datetime(nq_trades['date'])
    nq_trades = nq_trades[nq_trades['date_parsed'] >= '2026-01-01']
    nq_daily, nq_monthly = process_trades(nq_trades)
    nq_metrics = compute_metrics(nq_trades, nq_daily, '$')
    print('  {} trades | PnL ${:+,.0f}'.format(nq_metrics['total_trades'], nq_metrics['total_pnl']))
    generate_html(
        instrument='Nasdaq (MNQ) — Backtest',
        subtitle='Micro E-Mini Nasdaq 100 Futures  |  4 contrats  |  Backtest 2026',
        trades_df=nq_trades, daily_df=nq_daily, monthly_df=nq_monthly,
        metrics=nq_metrics, currency='$', accent_color='#2563eb',
        output_path='data/track_record_nasdaq_backtest.html',
    )

    # ── DAX ──
    print('\n=== DAX Backtest Track Record ===')
    df_dax = pd.read_csv('data/databento_fdxm_5min_12mo.csv', index_col=0, parse_dates=True)
    if df_dax.index.tz is None:
        df_dax.index = df_dax.index.tz_localize('UTC')
    cet_idx = df_dax.index.tz_convert(CET)
    mask = (cet_idx.hour >= 8) & ((cet_idx.hour < 17) | ((cet_idx.hour == 17) & (cet_idx.minute <= 25)))
    df_dax_2026 = df_dax[mask][df_dax[mask].index >= '2025-12-15']

    engine_dax = MM20BacktestEngine(
        tp_points=150, trail_bars=3, trail_delta_long=45,
        trail_bars_short=5, trail_delta_short=13,
        max_sl_pts=0, max_trades_day=4, sma_period=20,
        start_offset_min=0, abs_start_hour=10, abs_start_min=0,
        daily_loss_stop=3, point_value=10.0, daily_loss_usd=0,
        pullback_bars=10, pullback_dist=15, min_h1_sma_dist=75,
    )
    report_dax = engine_dax.run(df_dax_2026)
    dax_trades = pd.DataFrame(report_dax.trades)
    dax_trades['date_parsed'] = pd.to_datetime(dax_trades['date'])
    dax_trades = dax_trades[dax_trades['date_parsed'] >= '2026-01-01']
    dax_daily, dax_monthly = process_trades(dax_trades)
    dax_metrics = compute_metrics(dax_trades, dax_daily, 'EUR ')
    print('  {} trades | PnL EUR {:+,.0f}'.format(dax_metrics['total_trades'], dax_metrics['total_pnl']))
    generate_html(
        instrument='DAX 40 (FDXM) — Backtest',
        subtitle='Mini-DAX 40 Futures  |  2 contrats (10 EUR/pt)  |  Backtest 2026',
        trades_df=dax_trades, daily_df=dax_daily, monthly_df=dax_monthly,
        metrics=dax_metrics, currency='EUR ', accent_color='#d97706',
        output_path='data/track_record_dax_backtest.html',
    )
    print('\nDone!')


if __name__ == '__main__':
    import sys
    if '--backtest' in sys.argv:
        generate_backtest_track_records()
    else:
        generate_live_track_record()
