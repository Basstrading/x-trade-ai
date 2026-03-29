"""Test 3 micro-variantes EQUILIBREE — Topstep $150k."""
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
    from pathlib import Path

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
    print()

    variantes = {
        'EQUILIBREE BASE': {
            'tp_long': 80, 'sl_long': 60, 'tp_short': 60, 'sl_short': 45,
            'max_trades': 5, 'max_longs': 3, 'max_shorts': 2,
            'min_range': 20, 'max_range': 300,
        },
        'A - SL reduit': {
            'tp_long': 80, 'sl_long': 50, 'tp_short': 60, 'sl_short': 38,
            'max_trades': 5, 'max_longs': 3, 'max_shorts': 2,
            'min_range': 20, 'max_range': 300,
        },
        'B - 1 trade moins': {
            'tp_long': 80, 'sl_long': 60, 'tp_short': 60, 'sl_short': 45,
            'max_trades': 4, 'max_longs': 2, 'max_shorts': 2,
            'min_range': 20, 'max_range': 300,
        },
        'C - Filtre range': {
            'tp_long': 80, 'sl_long': 60, 'tp_short': 60, 'sl_short': 45,
            'max_trades': 5, 'max_longs': 3, 'max_shorts': 2,
            'min_range': 30, 'max_range': 250,
        },
    }

    print('=' * 108)
    print('MICRO-VARIANTES EQUILIBREE - TOPSTEP $150K')
    print('Objectif : DD < $4,500 ET Proj > $7,000/m ET PF > 1.3')
    print('=' * 108)
    header = (
        f'{"Variante":<22} '
        f'{"Trades":>7} '
        f'{"WR":>6} '
        f'{"PnL":>9} '
        f'{"PF":>5} '
        f'{"Sharpe":>7} '
        f'{"DD":>8} '
        f'{"J>1800":>7} '
        f'{"J>4500":>7} '
        f'{"Proj/m":>8} '
        f'{"Pass":>5}'
    )
    print(header)
    print('-' * 108)

    all_results = {}
    winner = None
    winner_score = 0

    for nom, params in variantes.items():
        engine = OPREngine(params=params)
        r = engine.run(df5, daily_loss_limit=AGENT_LIMIT, max_trades_per_day=params['max_trades'])
        if not r:
            print(f'{nom:<22} aucun trade')
            continue

        pnl_vals = list(r.daily_pnl.values())
        j_agent = sum(1 for p in pnl_vals if p < AGENT_LIMIT)
        j_topstep = sum(1 for p in pnl_vals if p < TOPSTEP_LIMIT)

        dd_ok = r.max_drawdown < 4500
        proj_ok = r.projected_monthly > 7000
        pf_ok = r.profit_factor > 1.3
        jt_ok = j_topstep == 0
        passes = dd_ok and proj_ok and pf_ok and jt_ok
        tag = 'YES' if passes else 'no'

        print(
            f'{nom:<22} '
            f'{r.total_trades:>7} '
            f'{r.win_rate:>5.1f}% '
            f'${r.total_pnl_dollars:>8.0f} '
            f'{r.profit_factor:>5.2f} '
            f'{r.sharpe_ratio:>7.2f} '
            f'${r.max_drawdown:>7.0f} '
            f'{j_agent:>7} '
            f'{j_topstep:>7} '
            f'${r.projected_monthly:>7.0f} '
            f'{tag:>5}'
        )

        all_results[nom] = {
            'report': r, 'j_agent': j_agent,
            'j_topstep': j_topstep, 'passes': passes,
        }

        if passes:
            score = r.profit_factor * r.projected_monthly
            if score > winner_score:
                winner_score = score
                winner = nom

    # Si aucun passe, prendre le meilleur DD
    if not winner:
        best_dd = 999999
        for nom, data in all_results.items():
            dd = data['report'].max_drawdown
            if dd < best_dd:
                best_dd = dd
                winner = nom

    print()
    data = all_results[winner]
    r = data['report']

    if data['passes']:
        print(f'GAGNANTE : {winner}')
    else:
        print(f'MOINS MAUVAISE : {winner}')

    dd_tag = 'Y' if r.max_drawdown < 4500 else 'N'
    proj_tag = 'Y' if r.projected_monthly > 7000 else 'N'
    pf_tag = 'Y' if r.profit_factor > 1.3 else 'N'
    jt_tag = 'Y' if data['j_topstep'] == 0 else 'N'

    print(f'  DD<4500 : ${r.max_drawdown:.0f} {dd_tag}')
    print(f'  Proj>7k : ${r.projected_monthly:.0f}/m {proj_tag}')
    print(f'  PF>1.3  : {r.profit_factor} {pf_tag}')
    print(f'  J>4500  : {data["j_topstep"]} {jt_tag}')
    print()

    # P&L jour par jour
    trades = r.trades
    print('P&L JOUR PAR JOUR')
    print('-' * 85)
    equity = 0
    for day, pnl in sorted(r.daily_pnl.items()):
        equity += pnl
        day_trades = [t for t in trades if t['entry_time'].startswith(day)]
        dirs = ''
        for t in day_trades:
            d_char = 'L' if t['direction'] == 'long' else 'S'
            ex = t['exit_reason'][:2].upper()
            dirs += d_char + ':' + ex + ' '
        sign = '+' if pnl >= 0 else '-'
        bar = '#' * min(int(abs(pnl) / 200), 15)
        flag = ''
        if pnl < TOPSTEP_LIMIT:
            flag = ' !! TOPSTEP'
        elif pnl < AGENT_LIMIT:
            flag = ' ! >AGENT'
        print(
            f'  {day} : {sign}${abs(pnl):7.0f} {bar:15s} '
            f'eq=${equity:+8.0f}  [{dirs.strip()}]{flag}'
        )

    print()
    print(f'Equity finale : ${equity:+.0f}')

    # Sauvegarde
    save = {
        'variante': winner,
        'params': variantes[winner],
        'trades': trades,
        'daily_pnl': r.daily_pnl,
        'total_trades': r.total_trades,
        'win_rate': r.win_rate,
        'total_pnl_dollars': r.total_pnl_dollars,
        'profit_factor': r.profit_factor,
        'max_drawdown': r.max_drawdown,
        'sharpe_ratio': r.sharpe_ratio,
        'projected_monthly': r.projected_monthly,
    }
    Path('data').mkdir(exist_ok=True)
    Path('data/opr_150k_winner.json').write_text(json.dumps(save, default=str))
    print('Sauvegarde -> data/opr_150k_winner.json')

    await client.logout()


asyncio.run(run())
