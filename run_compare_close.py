"""Compare Variante C: 22h50 vs 21h25 close time."""
import asyncio
import sys
import json
import os

sys.path.insert(0, '.')
from dotenv import load_dotenv
load_dotenv()

AGENT_LIMIT = -1800
TOPSTEP_LIMIT = -4500

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
    contract_id = contracts[0]['id']

    now = datetime.utcnow()
    all_5min = []
    for chunk_i in range((120 // 7) + 1):
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
        except Exception:
            pass

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
    print(f'{len(df5)} barres 5min')

    params_c = {
        'tp_long': 80, 'sl_long': 60, 'tp_short': 60, 'sl_short': 45,
        'max_trades': 5, 'max_longs': 3, 'max_shorts': 2,
        'min_range': 30, 'max_range': 250,
    }

    # Run with new 21h25 close (engine default now)
    engine = OPREngine(params=params_c)
    r = engine.run(df5, daily_loss_limit=AGENT_LIMIT, max_trades_per_day=5)

    if not r:
        print('Aucun trade')
        await client.logout()
        return

    pnl_vals = list(r.daily_pnl.values())
    j_agent = sum(1 for p in pnl_vals if p < AGENT_LIMIT)
    j_topstep = sum(1 for p in pnl_vals if p < TOPSTEP_LIMIT)

    # Count time_exit trades
    time_exits = [t for t in r.trades if t['exit_reason'] == 'time_exit']

    # Load old data for comparison
    old = json.loads(open('data/opr_150k_winner.json').read())
    old_time_exits = [t for t in old['trades'] if t['exit_reason'] == 'time_exit']

    print()
    print('=' * 70)
    print('VARIANTE C — IMPACT CLÔTURE 21h25 vs 22h50')
    print('=' * 70)
    print()
    print(f'{"Métrique":<25} {"AVANT (22h50)":>15} {"APRÈS (21h25)":>15}')
    print('-' * 70)
    print(f'{"Trades":<25} {old["total_trades"]:>15} {r.total_trades:>15}')
    print(f'{"Win Rate":<25} {old["win_rate"]:>14.1f}% {r.win_rate:>14.1f}%')
    print(f'{"PnL Total":<25} {"$" + str(int(old["total_pnl_dollars"])):>15} {"$" + str(int(r.total_pnl_dollars)):>15}')
    print(f'{"Profit Factor":<25} {old["profit_factor"]:>15.2f} {r.profit_factor:>15.2f}')
    print(f'{"Sharpe Ratio":<25} {old["sharpe_ratio"]:>15.2f} {r.sharpe_ratio:>15.2f}')
    print(f'{"Max Drawdown":<25} {"$" + str(int(old["max_drawdown"])):>15} {"$" + str(int(r.max_drawdown)):>15}')
    print(f'{"Jours > -$1800":<25} {4:>15} {j_agent:>15}')
    print(f'{"Jours > -$4500":<25} {0:>15} {j_topstep:>15}')
    print(f'{"Proj/mois":<25} {"$" + str(int(old["projected_monthly"])):>15} {"$" + str(int(r.projected_monthly)):>15}')
    print(f'{"Time exits":<25} {len(old_time_exits):>15} {len(time_exits):>15}')
    print()

    # Detail time_exit trades that changed
    print('TRADES TIME_EXIT — détail :')
    print('-' * 70)
    for t in time_exits:
        exit_h = t['exit_time'].split(' ')[1][:5]
        print(f'  {t["entry_time"][:16]}  {t["direction"]:5s}  exit={exit_h}  pnl=${t["pnl_dollars"]:+.0f}')

    print()
    print(f'Ancien time_exits (22h50) : {len(old_time_exits)}')
    print(f'Nouveau time_exits (21h25): {len(time_exits)}')

    # Identify trades that were time_exit in old but might be different now
    # by comparing exit times
    old_te_dates = set()
    for t in old_time_exits:
        d = t['entry_time'][:10]
        old_te_dates.add(d)
    print(f'Jours avec time_exit avant: {sorted(old_te_dates)}')

    new_te_dates = set()
    for t in time_exits:
        d = t['entry_time'][:10]
        new_te_dates.add(d)
    print(f'Jours avec time_exit après: {sorted(new_te_dates)}')

    # Critères pass/fail
    print()
    dd_ok = r.max_drawdown < 4500
    pf_ok = r.profit_factor > 1.3
    proj_ok = r.projected_monthly > 7000
    jt_ok = j_topstep == 0
    passes = dd_ok and pf_ok and proj_ok and jt_ok

    print(f'DD < $4,500  : {"OUI" if dd_ok else "NON"} (${r.max_drawdown:.0f})')
    print(f'PF > 1.3     : {"OUI" if pf_ok else "NON"} ({r.profit_factor})')
    print(f'Proj > $7,000: {"OUI" if proj_ok else "NON"} (${r.projected_monthly:.0f})')
    print(f'J > $4,500   : {"OUI" if jt_ok else "NON"} ({j_topstep})')
    print(f'VERDICT      : {"PASSE" if passes else "NE PASSE PAS"}')

    await client.logout()


asyncio.run(run())
