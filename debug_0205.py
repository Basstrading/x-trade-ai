"""Debug trades du 05/02/2026 — verifier SL dynamique et cap 200pts."""
import asyncio
import sys
import os

sys.path.insert(0, '.')
from dotenv import load_dotenv
load_dotenv()

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
    'sar_enabled': True,
    'close_hour': 20,
    'close_min': 49,
}

DAILY_LOSS_LIMIT = -4500


async def run():
    from projectx_api import ProjectXClient, ConnectionURLS, AggregationUnit
    from backtester.opr_engine import OPREngine
    import pandas as pd
    from datetime import datetime, timedelta

    TOPSTEPX_URLS = ConnectionURLS(
        api_endpoint='https://api.topstepx.com',
        user_hub='https://rtc.topstepx.com/hubs/user',
        market_hub='https://rtc.topstepx.com/hubs/market',
    )

    client = ProjectXClient(TOPSTEPX_URLS)
    await client.login({
        'auth_type': 'api_key',
        'userName': os.getenv('PROJECTX_USERNAME'),
        'apiKey': os.getenv('PROJECTX_API_KEY'),
    })

    contracts = await client.search_for_contracts(searchText='NQ', live=False)
    contract_id = contracts[0]['id'] if isinstance(contracts[0], dict) else contracts[0].id

    # Fetch just around 05/02/2026 (+/- few days for context)
    start = datetime(2026, 2, 3)
    end = datetime(2026, 2, 7)
    bars5 = await client.retrieve_bars(
        contractId=contract_id, live=False,
        startTime=start, endTime=end,
        unit=AggregationUnit.MINUTE, unitNumber=5,
        limit=10000, includePartialBar=False,
    )

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

    df5 = to_df(bars5)
    print(f'{len(df5)} barres chargees ({df5.index.min()} -> {df5.index.max()})')

    # Run backtest
    engine = OPREngine(params=PARAMS)
    r = engine.run(df5, daily_loss_limit=DAILY_LOSS_LIMIT, max_trades_per_day=PARAMS['max_trades'])

    if not r:
        print('Aucun trade')
        await client.logout()
        return

    # Filter trades on 2026-02-05
    trades_0205 = [t for t in r.trades if '2026-02-05' in t['entry_time']]

    print()
    print('=' * 90)
    print('DEBUG TRADES 05/02/2026')
    print(f'OPR Range: high={trades_0205[0]["range_high"] if trades_0205 else "?"} '
          f'low={trades_0205[0]["range_low"] if trades_0205 else "?"} '
          f'size={trades_0205[0]["range_size"] if trades_0205 else "?"}')
    print('=' * 90)

    daily_total = 0.0
    for i, t in enumerate(trades_0205, 1):
        sl_pts = t['sl_pts']
        capped = sl_pts >= 200  # was it capped?
        entry = t['entry_price']
        direction = t['direction']

        if direction == 'long':
            sl_price_calc = entry - sl_pts
            tp_price_calc = entry + t['tp_pts']
        else:
            sl_price_calc = entry + sl_pts
            tp_price_calc = entry - t['tp_pts']

        daily_total += t['pnl_dollars']

        print(f'\n--- Trade #{i} {"(SAR)" if t["is_sar"] else ""} ---')
        print(f'  Direction    : {direction.upper()}')
        print(f'  Entree       : {t["entry_time"][-14:]}  @ {entry:.2f}')
        print(f'  SL dynamique : {sl_pts:.2f} pts  -> SL price = {sl_price_calc:.2f}')
        print(f'  SL cap 200   : {"OUI (plafonne)" if capped else "NON (< 200)"}')
        if sl_pts > 200:
            print(f'  !! BUG !!    : SL = {sl_pts:.2f} pts > 200 pts cap !')
        print(f'  TP           : {t["tp_pts"]:.2f} pts  -> TP price = {tp_price_calc:.2f}')
        print(f'  Sortie       : {t["exit_time"][-14:]}  @ {t["exit_price"]:.2f}')
        print(f'  Raison       : {t["exit_reason"]}')
        print(f'  P&L          : {t["pnl_points"]:+.2f} pts = ${t["pnl_dollars"]:+,.0f}')
        print(f'  Barres held  : {t["bars_held"]}')
        print(f'  Cumul jour   : ${daily_total:+,.0f}')

    print()
    print('=' * 90)
    print(f'TOTAL JOUR 05/02 : ${daily_total:+,.0f}  ({len(trades_0205)} trades)')
    print('=' * 90)

    # Check ALL trades for SL > 200
    print()
    print('--- VERIFICATION SL > 200 pts SUR TOUS LES TRADES ---')
    bugs = [t for t in r.trades if t['sl_pts'] > 200]
    if bugs:
        print(f'!! {len(bugs)} TRADES AVEC SL > 200 pts :')
        for t in bugs:
            print(f'  {t["entry_time"]} {t["direction"]} SL={t["sl_pts"]:.2f}pts entry={t["entry_price"]}')
    else:
        print('OK : Aucun trade avec SL > 200 pts')

    await client.logout()


asyncio.run(run())
