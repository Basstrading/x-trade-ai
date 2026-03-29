"""Backtest 120 jours NQ — Moteur Dalton Pur + regles Topstep $50k."""
import asyncio
import sys
import json
import os

sys.path.insert(0, '.')
from dotenv import load_dotenv
load_dotenv()

# Regles Topstep $50k
TOPSTEP_50K = {
    'daily_loss_topstep': -2000,
    'daily_loss_agent': -900,
    'trailing_dd': -3000,
    'max_trades_day': 4,
}


async def run():
    from projectx_api import ProjectXClient, ConnectionURLS, AggregationUnit
    from backtester.engine import DaltonEngine
    import pandas as pd
    from datetime import datetime, timedelta
    from pathlib import Path

    DAYS = 120

    TOPSTEPX_URLS = ConnectionURLS(
        api_endpoint='https://api.topstepx.com',
        user_hub='https://rtc.topstepx.com/hubs/user',
        market_hub='https://rtc.topstepx.com/hubs/market',
    )

    print('=' * 60)
    print(f'BACKTEST {DAYS} JOURS — DALTON PUR — TOPSTEP $50K')
    print(f'Daily limit agent: ${TOPSTEP_50K["daily_loss_agent"]}')
    print(f'Max trades/jour: {TOPSTEP_50K["max_trades_day"]}')
    print('=' * 60)
    print()

    print('Connexion ProjectX...')
    client = ProjectXClient(TOPSTEPX_URLS)
    await client.login({
        'auth_type': 'api_key',
        'userName': os.getenv('PROJECTX_USERNAME'),
        'apiKey': os.getenv('PROJECTX_API_KEY'),
    })

    contracts = await client.search_for_contracts(searchText='NQ', live=False)
    contract_id = contracts[0]['id']
    print(f'Connecte — NQ id={contract_id}')

    # Fetch data in 7-day reverse chunks
    now = datetime.utcnow()
    all_1min = []
    all_5min = []
    n_chunks = (DAYS // 7) + 1

    print('Recuperation donnees reelles...')
    for chunk_i in range(n_chunks):
        chunk_end = now - timedelta(days=chunk_i * 7)
        chunk_start = chunk_end - timedelta(days=7)

        try:
            bars = await client.retrieve_bars(
                contractId=contract_id, live=False,
                startTime=chunk_start, endTime=chunk_end,
                unit=AggregationUnit.MINUTE, unitNumber=1,
                limit=10000, includePartialBar=False
            )
            if bars:
                all_1min.extend(bars)
        except Exception as e:
            print(f'  1min chunk {chunk_i} err: {e}')

        try:
            bars5 = await client.retrieve_bars(
                contractId=contract_id, live=False,
                startTime=chunk_start, endTime=chunk_end,
                unit=AggregationUnit.MINUTE, unitNumber=5,
                limit=10000, includePartialBar=False
            )
            if bars5:
                all_5min.extend(bars5)
        except Exception as e:
            print(f'  5min chunk {chunk_i} err: {e}')

    def to_df(bars):
        data = []
        for b in bars:
            d = b if isinstance(b, dict) else b.__dict__
            data.append({
                'datetime': d.get('t') or d.get('datetime') or d.get('timestamp'),
                'open': float(d.get('o') or d.get('open') or 0),
                'high': float(d.get('h') or d.get('high') or 0),
                'low': float(d.get('l') or d.get('low') or 0),
                'close': float(d.get('c') or d.get('close') or 0),
                'volume': float(d.get('v') or d.get('volume') or 1),
            })
        df = pd.DataFrame(data)
        df['datetime'] = pd.to_datetime(df['datetime'], utc=True)
        df = df.sort_values('datetime').drop_duplicates('datetime')
        return df.set_index('datetime')

    df_1min = to_df(all_1min)
    df_5min = to_df(all_5min)

    print(f'Barres 1min : {len(df_1min)}')
    print(f'Barres 5min : {len(df_5min)}')
    print()

    options = {'hvl': 24820, 'call_wall': 24900, 'put_wall': 24000}

    # Backtest Dalton
    engine = DaltonEngine()
    report = engine.run(
        df_1min, df_5min,
        options_levels=options,
        daily_loss_limit=TOPSTEP_50K['daily_loss_agent'],
        max_trades_per_day=TOPSTEP_50K['max_trades_day'],
    )

    if not report:
        print('Aucun trade genere')
        await client.logout()
        return

    # ---- RESULTATS GLOBAUX ----
    print('=' * 60)
    print('RESULTATS GLOBAUX')
    print('=' * 60)
    print(f'Trades total    : {report.total_trades}')
    print(f'Trades gagnants : {report.winning_trades}')
    print(f'Trades perdants : {report.losing_trades}')
    print(f'Win Rate        : {report.win_rate}%')
    print()
    print(f'P&L Total       : ${report.total_pnl_dollars:+.2f}')
    print(f'Avg Win         : ${report.avg_win_dollars:.2f}')
    print(f'Avg Loss        : ${report.avg_loss_dollars:.2f}')
    avg_l = max(report.avg_loss_dollars, 1)
    print(f'Ratio W/L       : {report.avg_win_dollars/avg_l:.2f}:1')
    print()
    print(f'Profit Factor   : {report.profit_factor}')
    print(f'Expectancy      : ${report.expectancy:.2f}/trade')
    print(f'Sharpe          : {report.sharpe_ratio}')
    print(f'Max Drawdown    : ${report.max_drawdown:.2f}')
    print()

    # ---- PAR SIGNAL DALTON ----
    print('=' * 60)
    print('PAR SIGNAL DALTON')
    print('=' * 60)
    for sig, count in sorted(report.trades_by_signal.items()):
        wr = report.winrate_by_signal.get(sig, 0)
        print(f'  {sig:25s} : {count:3d} trades  {wr:5.1f}% WR')
    print()

    # ---- PAR DAY TYPE ----
    print('=' * 60)
    print('PAR DAY TYPE')
    print('=' * 60)
    for dt, count in sorted(report.trades_by_daytype.items()):
        wr = report.winrate_by_daytype.get(dt, 0)
        print(f'  {dt:25s} : {count:3d} trades  {wr:5.1f}% WR')
    print()

    # ---- EXITS ----
    print('=' * 60)
    print('EXITS')
    print('=' * 60)
    total = max(report.total_trades, 1)
    print(f'Stop           : {report.exits_stop} ({report.exits_stop/total*100:.0f}%)')
    print(f'Target         : {report.exits_target} ({report.exits_target/total*100:.0f}%)')
    print(f'Breakeven      : {report.exits_breakeven} ({report.exits_breakeven/total*100:.0f}%)')
    print(f'Session end    : {report.exits_session} ({report.exits_session/total*100:.0f}%)')
    print(f'Daily limit    : {report.exits_daily_limit} ({report.exits_daily_limit/total*100:.0f}%)')
    print()

    # ---- ANALYSE TOPSTEP $50K ----
    print('=' * 60)
    print('ANALYSE TOPSTEP $50K')
    print('=' * 60)
    print(f'Jours trades      : {report.days_traded}')
    print(f'Jours profitables : {report.days_profitable}')
    print(f'Jours perdants    : {report.days_losing}')
    print(f'Meilleur jour     : ${report.best_day:+.0f}')
    print(f'Pire jour         : ${report.worst_day:+.0f}')
    print()
    print(f'Jours > limite agent   : {report.days_over_agent_limit}')
    print(f'Jours > limite Topstep : {report.days_over_topstep_limit}')
    print()
    print(f'Avg P&L/jour    : ${report.avg_daily_pnl:+.0f}')
    print(f'Projection mois : ${report.projected_monthly:+.0f}')
    print(f'ROI mensuel     : {report.projected_monthly/50000*100:+.1f}%')
    print()

    # ---- P&L PAR JOUR ----
    print('=' * 60)
    print('P&L PAR JOUR')
    print('=' * 60)
    daily_pnls = report.daily_pnl
    for day, pnl in sorted(daily_pnls.items()):
        bar_len = int(abs(pnl) / 50)
        bar = '#' * min(bar_len, 20)
        sign = '+' if pnl >= 0 else '-'
        flag = ''
        if pnl < -2000:
            flag = ' !! >LIMITE TOPSTEP'
        elif pnl < TOPSTEP_50K['daily_loss_agent']:
            flag = ' ! >LIMITE AGENT'
        print(f'  {day} : {sign}${abs(pnl):6.0f} {bar}{flag}')
    print()

    # ---- MEILLEURS / PIRES TRADES ----
    trades = report.trades
    if trades:
        sorted_trades = sorted(trades, key=lambda x: x['pnl_dollars'], reverse=True)
        print('=' * 60)
        print('5 MEILLEURS TRADES')
        print('=' * 60)
        for t in sorted_trades[:5]:
            print(
                f"  {t['direction']:5} {t['signal_type']:22} "
                f"DT:{t['day_type']:12} "
                f"PnL: ${t['pnl_dollars']:+7.0f} "
                f"Exit: {t['exit_reason']:12} "
                f"Bars: {t['bars_held']}"
            )
        print()
        print('=' * 60)
        print('5 PIRES TRADES')
        print('=' * 60)
        for t in sorted_trades[-5:]:
            print(
                f"  {t['direction']:5} {t['signal_type']:22} "
                f"DT:{t['day_type']:12} "
                f"PnL: ${t['pnl_dollars']:+7.0f} "
                f"Exit: {t['exit_reason']:12} "
                f"Bars: {t['bars_held']}"
            )

    # ---- COMPARAISON AVANT/APRES ----
    print('=' * 60)
    print('COMPARAISON AVANT / APRES (stops recalibres)')
    print('=' * 60)
    print(f'{"Metrique":<20} {"AVANT":>10} {"APRES":>10} {"Delta":>10}')
    print('-' * 50)
    comparisons = [
        ('Trades', 66, report.total_trades),
        ('Win Rate %', 31.8, report.win_rate),
        ('P&L $', -4782, report.total_pnl_dollars),
        ('PF', 0.77, report.profit_factor),
        ('Sharpe', -1.41, report.sharpe_ratio),
        ('Max DD $', 8845, report.max_drawdown),
        ('Jours >-900', 8, report.days_over_agent_limit),
    ]
    for name, avant, apres in comparisons:
        delta = apres - avant
        sign = '+' if delta > 0 else ''
        print(f'{name:<20} {avant:>10.1f} {apres:>10.1f} {sign}{delta:>9.1f}')
    print()

    # VERDICT
    if report.profit_factor >= 1.3 and report.max_drawdown < 3000:
        print('VERDICT: PF > 1.3 ET DD < $3,000')
    elif report.profit_factor >= 1.0:
        print('VERDICT: PF > 1.0 MAIS DD ENCORE ELEVE')
    else:
        print('VERDICT: PF < 1.0')
    print()

    # ---- SAUVEGARDE ----
    print()
    rd = {
        'engine': 'DaltonPure',
        'account': '50k',
        'total_trades': report.total_trades,
        'win_rate': report.win_rate,
        'total_pnl_dollars': report.total_pnl_dollars,
        'profit_factor': report.profit_factor,
        'sharpe_ratio': report.sharpe_ratio,
        'max_drawdown': report.max_drawdown,
        'expectancy': report.expectancy,
        'trades_by_signal': report.trades_by_signal,
        'winrate_by_signal': report.winrate_by_signal,
        'trades_by_daytype': report.trades_by_daytype,
        'winrate_by_daytype': report.winrate_by_daytype,
        'exits': {
            'stop': report.exits_stop,
            'target': report.exits_target,
            'breakeven': report.exits_breakeven,
            'session_end': report.exits_session,
            'daily_limit': report.exits_daily_limit,
        },
        'topstep_analysis': {
            'days_traded': report.days_traded,
            'days_profitable': report.days_profitable,
            'days_losing': report.days_losing,
            'best_day': report.best_day,
            'worst_day': report.worst_day,
            'days_over_agent_limit': report.days_over_agent_limit,
            'days_over_topstep_limit': report.days_over_topstep_limit,
            'avg_daily_pnl': report.avg_daily_pnl,
            'projected_monthly': report.projected_monthly,
        },
        'daily_pnl': report.daily_pnl,
        'trades': report.trades,
    }
    Path('data').mkdir(exist_ok=True)
    Path(f'data/backtest_dalton_{DAYS}days.json').write_text(json.dumps(rd, default=str))
    Path('data/last_backtest.json').write_text(json.dumps(rd, default=str))
    print('Rapport sauvegarde')
    print(f'  -> data/backtest_dalton_{DAYS}days.json')
    print('  -> data/last_backtest.json')

    await client.logout()
    print('\nTermine.')


asyncio.run(run())
