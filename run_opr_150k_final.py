"""Backtest OPR FINAL — Topstep $150k — NQ standard $20/pt x1."""
import asyncio
import sys
import os

sys.path.insert(0, '.')
from dotenv import load_dotenv
load_dotenv()

# ── CONFIG PROD ──────────────────────────────────────────
PARAMS = {
    'tp_long': 217.75,
    'tp_short': 205.75,
    'sl_type': 'periods_high_low',
    'sl_long_periods': 9,
    'sl_long_delta': -41.75,
    'sl_short_periods': 15,
    'sl_short_delta': 0.25,
    'sl_max_pts': 200,
    'max_trades': 6,
    'max_longs': 3,
    'max_shorts': 3,
    'min_range': 15,
    'max_range': 999,
    'point_value': 20.0,
    'contracts': 1,
    'sar_enabled': False,
    'close_hour': 20,
    'close_min': 49,
}

DAILY_LOSS_LIMIT = -4500
TOPSTEP_LIMIT = -4500
DAYS = 120

JOURS_FR = {0: 'LUN', 1: 'MAR', 2: 'MER', 3: 'JEU', 4: 'VEN', 5: 'SAM', 6: 'DIM'}


async def run():
    from projectx_api import ProjectXClient, ConnectionURLS, AggregationUnit
    from backtester.opr_engine import OPREngine
    import pandas as pd
    import numpy as np
    from datetime import datetime, timedelta

    TOPSTEPX_URLS = ConnectionURLS(
        api_endpoint='https://api.topstepx.com',
        user_hub='https://rtc.topstepx.com/hubs/user',
        market_hub='https://rtc.topstepx.com/hubs/market',
    )

    print('=' * 80)
    print('BACKTEST OPR FINAL — TOPSTEP $150K — NQ $20/pt x1')
    print('=' * 80)

    # ── CONNEXION ──
    print('Connexion ProjectX...')
    client = ProjectXClient(TOPSTEPX_URLS)
    await client.login({
        'auth_type': 'api_key',
        'userName': os.getenv('PROJECTX_USERNAME'),
        'apiKey': os.getenv('PROJECTX_API_KEY'),
    })

    contracts = await client.search_for_contracts(searchText='NQ', live=False)
    contract_id = contracts[0]['id'] if isinstance(contracts[0], dict) else contracts[0].id
    print(f'NQ contract id={contract_id}')

    # ── FETCH DATA ──
    now = datetime.utcnow()
    all_5min = []
    n_chunks = (DAYS // 7) + 1

    print(f'Recuperation {DAYS}j de barres 5min ({n_chunks} chunks)...')
    for chunk_i in range(n_chunks):
        chunk_end = now - timedelta(days=chunk_i * 7)
        chunk_start = chunk_end - timedelta(days=7)
        try:
            bars5 = await client.retrieve_bars(
                contractId=contract_id, live=False,
                startTime=chunk_start, endTime=chunk_end,
                unit=AggregationUnit.MINUTE, unitNumber=5,
                limit=10000, includePartialBar=False,
            )
            if bars5:
                all_5min.extend(bars5)
                print(f'  chunk {chunk_i+1}/{n_chunks}: {len(bars5)} barres')
        except Exception as e:
            print(f'  chunk {chunk_i+1} err: {e}')

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

    df5 = to_df(all_5min)
    print(f'\n{len(df5)} barres 5min chargees')
    print(f'Periode: {df5.index.min()} -> {df5.index.max()}')

    # ── BACKTEST ──
    print('\nLancement backtest...')
    engine = OPREngine(params=PARAMS)
    r = engine.run(df5, daily_loss_limit=DAILY_LOSS_LIMIT, max_trades_per_day=PARAMS['max_trades'])

    if not r:
        print('AUCUN TRADE — verifier les donnees.')
        await client.logout()
        return

    # ── RESULTATS GLOBAUX ──
    pnl_vals = list(r.daily_pnl.values())
    days_over_limit = sum(1 for p in pnl_vals if p < TOPSTEP_LIMIT)

    print()
    print('=' * 80)
    print('RESULTATS GLOBAUX')
    print('=' * 80)
    print(f'  Trades       : {r.total_trades} (L:{r.long_trades} S:{r.short_trades})')
    print(f'  Win Rate     : {r.win_rate}% (L:{r.long_winrate}% S:{r.short_winrate}%)')
    print(f'  P&L total    : ${r.total_pnl_dollars:+,.0f}')
    print(f'  Profit Factor: {r.profit_factor}')
    print(f'  Sharpe Ratio : {r.sharpe_ratio}')
    print(f'  Max Drawdown : ${r.max_drawdown:,.0f}')
    print(f'  Expectancy   : ${r.expectancy:,.2f}/trade')
    print(f'  Avg Win      : ${r.avg_win_dollars:,.0f}   Avg Loss: ${r.avg_loss_dollars:,.0f}')
    total = max(r.total_trades, 1)
    print(f'  Exits        : TP={r.exits_tp}({r.exits_tp/total*100:.0f}%) '
          f'SL={r.exits_sl}({r.exits_sl/total*100:.0f}%) '
          f'Time={r.exits_time}({r.exits_time/total*100:.0f}%)')
    print(f'  SAR          : {r.sar_trades} trades, {r.sar_wins} wins, ${r.sar_pnl_dollars:+,.0f}')
    print()
    print(f'  Jours trades : {r.days_traded}')
    print(f'  Jours profit : {r.days_profitable}')
    print(f'  Jours perte  : {r.days_losing}')
    print(f'  Best day     : ${r.best_day:+,.0f}')
    print(f'  Worst day    : ${r.worst_day:+,.0f}')
    print(f'  Jours > -$4,500 : {days_over_limit}')
    print(f'  Avg daily    : ${r.avg_daily_pnl:+,.0f}')
    print(f'  Proj/mois    : ${r.projected_monthly:+,.0f} (21j)')

    # ── P&L JOUR PAR JOUR ──
    print()
    print('=' * 80)
    print('P&L JOUR PAR JOUR')
    print('=' * 80)

    sorted_days = sorted(r.daily_pnl.items())
    equity = 0.0
    current_month = None
    month_pnl = 0.0

    for day_str, pnl in sorted_days:
        # Detect month change
        month_key = day_str[:7]  # YYYY-MM
        if current_month is not None and month_key != current_month:
            print(f'  {"":>10} {"":>3}   {"--- TOTAL " + current_month + " ---":>20}  ${month_pnl:>+10,.0f}')
            print()
            month_pnl = 0.0
        current_month = month_key

        equity += pnl
        month_pnl += pnl

        # Day of week
        from datetime import date
        parts = day_str.split('-')
        d = date(int(parts[0]), int(parts[1]), int(parts[2]))
        jour = JOURS_FR.get(d.weekday(), '???')

        flag = ' !! ALERTE' if pnl < TOPSTEP_LIMIT else ''
        print(f'  {day_str} {jour} : ${pnl:>+10,.0f}  eq=${equity:>+10,.0f}{flag}')

    # Last month total
    if current_month:
        print(f'  {"":>10} {"":>3}   {"--- TOTAL " + current_month + " ---":>20}  ${month_pnl:>+10,.0f}')

    print()
    print('=' * 80)
    print(f'EQUITY FINALE : ${equity:+,.0f}')
    print('=' * 80)

    await client.logout()
    print('\nTermine.')


asyncio.run(run())
