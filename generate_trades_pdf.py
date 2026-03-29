"""Generate PDF report of all MM20 Pullback trades — NQ 12 derniers mois."""
import sys
sys.path.insert(0, '.')

import pandas as pd
import numpy as np
from fpdf import FPDF
from backtester.mm20_engine import MM20BacktestEngine


class TradesPDF(FPDF):
    def header(self):
        self.set_font('Helvetica', 'B', 14)
        self.cell(0, 8, 'MM20 PULLBACK  -  MNQ x4 (8 USD/pt)', new_x="LMARGIN", new_y="NEXT", align='C')
        self.set_font('Helvetica', '', 9)
        self.cell(0, 5, 'Periode: Mars 2025 - Mars 2026  |  TP 300 / SL 200 / Trail 20 / H1 dist >= 75 / Daily cap $1,000', new_x="LMARGIN", new_y="NEXT", align='C')
        self.ln(2)

    def footer(self):
        self.set_y(-15)
        self.set_font('Helvetica', 'I', 7)
        self.cell(0, 10, 'Page {}'.format(self.page_no()), align='C')

    def month_header(self, month_str, stats):
        self.set_fill_color(40, 40, 60)
        self.set_text_color(255, 255, 255)
        self.set_font('Helvetica', 'B', 10)
        self.cell(0, 7, '  {}  |  {}t ({}W/{}L)  |  WR {:.0f}%  |  PnL ${:+,.0f}'.format(
            month_str, stats['n'], stats['w'], stats['l'], stats['wr'], stats['pnl']
        ), new_x="LMARGIN", new_y="NEXT", fill=True)
        self.ln(1)

    def table_header(self):
        self.set_font('Helvetica', 'B', 7)
        self.set_fill_color(220, 220, 230)
        self.set_text_color(0, 0, 0)
        cols = [
            ('Date', 22), ('Dir', 10), ('Entree', 16), ('Heure E', 24),
            ('Sortie', 16), ('Heure S', 24), ('Raison', 18),
            ('Pts', 16), ('P&L $', 20), ('Cumul $', 22),
        ]
        for name, w in cols:
            self.cell(w, 5, name, border=1, fill=True, align='C')
        self.ln()

    def trade_row(self, t, cumul, is_alt):
        self.set_font('Helvetica', '', 6.5)
        if t['pnl_usd'] > 0:
            self.set_text_color(0, 120, 0)
        elif t['pnl_usd'] < 0:
            self.set_text_color(200, 0, 0)
        else:
            self.set_text_color(80, 80, 80)

        if is_alt:
            self.set_fill_color(245, 245, 250)
        else:
            self.set_fill_color(255, 255, 255)

        entry_time = str(t['entry_time'])[-14:].strip()
        exit_time = str(t['exit_time'])[-14:].strip()
        # Trim to just HH:MM
        if len(entry_time) > 5:
            entry_time = entry_time[:16] if len(entry_time) > 16 else entry_time
        if len(exit_time) > 5:
            exit_time = exit_time[:16] if len(exit_time) > 16 else exit_time

        cols = [
            (str(t['date']), 22, 'C'),
            (t['direction'].upper(), 10, 'C'),
            ('{:.0f}'.format(t['entry']), 16, 'R'),
            (entry_time[-8:] if len(entry_time) > 8 else entry_time, 24, 'C'),
            ('{:.0f}'.format(t['exit']), 16, 'R'),
            (exit_time[-8:] if len(exit_time) > 8 else exit_time, 24, 'C'),
            (t['exit_reason'], 18, 'C'),
            ('{:+.0f}'.format(t['pnl_pts']), 16, 'R'),
            ('${:+,.0f}'.format(t['pnl_usd']), 20, 'R'),
            ('${:+,.0f}'.format(cumul), 22, 'R'),
        ]
        for val, w, align in cols:
            self.cell(w, 4.5, val, border=1, fill=is_alt, align=align)
        self.ln()

    def day_separator(self, day_str, day_pnl, day_trades, day_wins):
        self.set_font('Helvetica', 'B', 6.5)
        if day_pnl > 0:
            self.set_fill_color(220, 245, 220)
            self.set_text_color(0, 100, 0)
        elif day_pnl < 0:
            self.set_fill_color(245, 220, 220)
            self.set_text_color(180, 0, 0)
        else:
            self.set_fill_color(240, 240, 240)
            self.set_text_color(80, 80, 80)
        marker = '+' if day_pnl > 0 else ('-' if day_pnl < 0 else '=')
        self.cell(0, 4, '  {} | {}t ({}W/{}L) | ${:+,.0f}  {}'.format(
            day_str, day_trades, day_wins, day_trades - day_wins, day_pnl, marker
        ), new_x="LMARGIN", new_y="NEXT", fill=True)

    def summary_page(self, trades_df, equity):
        self.add_page()
        self.set_font('Helvetica', 'B', 14)
        self.cell(0, 10, 'RESUME', new_x="LMARGIN", new_y="NEXT", align='C')
        self.ln(5)

        pnls = trades_df['pnl_usd'].tolist()
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        gw = sum(wins) if wins else 0
        gl = abs(sum(losses)) if losses else 1
        pf = round(gw / gl, 2) if gl > 0 else 99
        eq = np.array(equity)
        peak = np.maximum.accumulate(eq)
        dd = abs((eq - peak).min())
        sharpe = round(np.mean(pnls) / np.std(pnls) * np.sqrt(252), 2) if np.std(pnls) > 0 else 0

        stats = [
            ('Trades', str(len(pnls))),
            ('Trades gagnants', '{} ({:.1f}%)'.format(len(wins), len(wins)/len(pnls)*100)),
            ('Trades perdants', str(len(losses))),
            ('PnL Total', '${:+,.0f}'.format(sum(pnls))),
            ('Profit Factor', str(pf)),
            ('Sharpe Ratio', str(sharpe)),
            ('Trade gagnant moyen', '${:+,.0f} ({:+.0f} pts)'.format(np.mean(wins), np.mean([p for p in trades_df[trades_df['pnl_usd']>0]['pnl_pts']]))),
            ('Trade perdant moyen', '${:,.0f} ({:.0f} pts)'.format(np.mean(losses), np.mean([p for p in trades_df[trades_df['pnl_usd']<0]['pnl_pts']]))),
            ('Gain brut', '${:,.0f}'.format(gw)),
            ('Perte brute', '${:,.0f}'.format(gl)),
            ('Max Drawdown', '${:,.0f}'.format(dd)),
            ('Meilleur trade', '${:+,.0f}'.format(max(pnls))),
            ('Pire trade', '${:+,.0f}'.format(min(pnls))),
        ]

        self.set_font('Helvetica', '', 10)
        for label, val in stats:
            self.set_font('Helvetica', 'B', 10)
            self.cell(60, 7, label, border=1, fill=False)
            self.set_font('Helvetica', '', 10)
            self.cell(60, 7, val, border=1, new_x="LMARGIN", new_y="NEXT")

        # Monthly summary
        self.ln(8)
        self.set_font('Helvetica', 'B', 12)
        self.cell(0, 8, 'P&L MENSUEL', new_x="LMARGIN", new_y="NEXT", align='C')
        self.ln(3)

        self.set_font('Helvetica', 'B', 9)
        self.set_fill_color(40, 40, 60)
        self.set_text_color(255, 255, 255)
        for h, w in [('Mois', 25), ('Trades', 15), ('W', 10), ('L', 10), ('WR', 15), ('PnL', 25), ('Cumul', 25)]:
            self.cell(w, 6, h, border=1, fill=True, align='C')
        self.ln()

        self.set_text_color(0, 0, 0)
        trades_df['month'] = trades_df['date_parsed'].dt.to_period('M')
        cumul = 0
        for m, g in trades_df.groupby('month'):
            mp = g['pnl_usd'].sum()
            cumul += mp
            nt = len(g)
            nw = int((g['pnl_usd'] > 0).sum())
            wr = nw / nt * 100

            if mp > 0:
                self.set_fill_color(220, 245, 220)
            else:
                self.set_fill_color(245, 220, 220)

            self.set_font('Helvetica', '', 9)
            self.cell(25, 5.5, str(m), border=1, fill=True, align='C')
            self.cell(15, 5.5, str(nt), border=1, fill=True, align='C')
            self.cell(10, 5.5, str(nw), border=1, fill=True, align='C')
            self.cell(10, 5.5, str(nt - nw), border=1, fill=True, align='C')
            self.cell(15, 5.5, '{:.0f}%'.format(wr), border=1, fill=True, align='C')
            self.cell(25, 5.5, '${:+,.0f}'.format(mp), border=1, fill=True, align='R')
            self.cell(25, 5.5, '${:+,.0f}'.format(cumul), border=1, fill=True, align='R')
            self.ln()


def main():
    # Load data and run backtest
    print('Loading NQ 5min data...')
    df = pd.read_csv('data/databento_nq_5min_5y.csv', index_col=0, parse_dates=True)
    if df.index.tz is None:
        df.index = df.index.tz_localize('UTC')

    df_12m = df[df.index >= '2025-03-01']

    print('Running backtest...')
    engine = MM20BacktestEngine(
        tp_points=300, trail_bars=20, max_sl_pts=200, max_trades_day=4,
        sma_period=20, start_offset_min=30, abs_start_hour=0,
        daily_loss_stop=3, point_value=8.0, daily_loss_usd=1000,
        pullback_bars=10, pullback_dist=15, min_h1_sma_dist=75,
    )
    report = engine.run(df_12m)

    trades_df = pd.DataFrame(report.trades)
    trades_df['date_parsed'] = pd.to_datetime(trades_df['date'])
    trades_df['month'] = trades_df['date_parsed'].dt.to_period('M')

    print('Generating PDF ({} trades)...'.format(len(trades_df)))

    pdf = TradesPDF('L', 'mm', 'A4')  # Landscape
    pdf.set_auto_page_break(auto=True, margin=15)

    # Page 1: Summary
    pdf.summary_page(trades_df, report.equity_curve)

    # Trade pages by month
    cumul = 0.0
    for month, month_group in trades_df.groupby('month'):
        pdf.add_page()

        m_pnl = month_group['pnl_usd'].sum()
        m_wins = int((month_group['pnl_usd'] > 0).sum())
        m_stats = {
            'n': len(month_group), 'w': m_wins,
            'l': len(month_group) - m_wins,
            'wr': m_wins / len(month_group) * 100,
            'pnl': m_pnl,
        }
        pdf.month_header(str(month), m_stats)
        pdf.table_header()

        prev_date = None
        row_idx = 0
        for _, t in month_group.iterrows():
            # Check page space
            if pdf.get_y() > 180:
                pdf.add_page()
                pdf.month_header(str(month) + ' (suite)', m_stats)
                pdf.table_header()

            # Day separator
            if prev_date is not None and t['date'] != prev_date:
                day_trades = month_group[month_group['date'] == prev_date]
                day_pnl = day_trades['pnl_usd'].sum()
                day_wins = int((day_trades['pnl_usd'] > 0).sum())
                pdf.day_separator(prev_date, day_pnl, len(day_trades), day_wins)
                pdf.ln(1)
                row_idx = 0

            cumul += t['pnl_usd']
            pdf.trade_row(t, cumul, row_idx % 2 == 1)
            prev_date = t['date']
            row_idx += 1

        # Last day separator
        if prev_date:
            day_trades = month_group[month_group['date'] == prev_date]
            day_pnl = day_trades['pnl_usd'].sum()
            day_wins = int((day_trades['pnl_usd'] > 0).sum())
            pdf.day_separator(prev_date, day_pnl, len(day_trades), day_wins)

    # Save
    output_path = 'data/MM20_Pullback_NQ_Trades_12mois.pdf'
    pdf.output(output_path)
    print('PDF saved: {}'.format(output_path))


if __name__ == '__main__':
    main()
