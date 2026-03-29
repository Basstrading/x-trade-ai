"""Backtest OPR config VIDEO optimisée — MNQ x2 contrats — 120j."""
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
    print(f'{len(df5)} barres 5min chargees')
    print()

    params = {
        'tp_long': 252,
        'sl_long': 273,
        'tp_short': 198,
        'sl_short': 116,
        'max_trades': 9,
        'max_longs': 5,
        'max_shorts': 4,
        'min_range': 15,
        'max_range': 999,
        'close_hour': 21,
        'close_min': 44,
        'point_value': 2.0,
        'contracts': 2,
    }

    engine = OPREngine(params=params)
    r = engine.run(df5, daily_loss_limit=AGENT_LIMIT, max_trades_per_day=9)

    if not r:
        print('Aucun trade')
        await client.logout()
        return

    pnl_vals = list(r.daily_pnl.values())
    j_agent = sum(1 for p in pnl_vals if p < AGENT_LIMIT)
    j_topstep = sum(1 for p in pnl_vals if p < TOPSTEP_LIMIT)

    print('=' * 85)
    print('BACKTEST OPR — CONFIG VIDEO OPTIMISEE — MNQ x2')
    print('=' * 85)
    p = params
    print(f'  Instrument  : MNQ (Micro NQ) x {p["contracts"]} contrats')
    print(f'  Point value : ${p["point_value"]} x {p["contracts"]} = ${p["point_value"] * p["contracts"]}/pt')
    print(f'  TP Long     : {p["tp_long"]} pts (${p["tp_long"] * p["point_value"] * p["contracts"]:.0f})')
    print(f'  SL Long     : {p["sl_long"]} pts (${p["sl_long"] * p["point_value"] * p["contracts"]:.0f})')
    print(f'  TP Short    : {p["tp_short"]} pts (${p["tp_short"] * p["point_value"] * p["contracts"]:.0f})')
    print(f'  SL Short    : {p["sl_short"]} pts (${p["sl_short"] * p["point_value"] * p["contracts"]:.0f})')
    print(f'  Max trades  : {p["max_trades"]} (L:{p["max_longs"]} S:{p["max_shorts"]})')
    print(f'  Range       : {p["min_range"]}-{p["max_range"]} pts')
    print(f'  Close       : {p["close_hour"]}h{p["close_min"]:02d}')
    print(f'  Daily limit : ${AGENT_LIMIT}')
    print('=' * 85)
    print()

    print('RESULTATS')
    print('-' * 50)
    print(f'  Trades       : {r.total_trades} (L:{r.long_trades} S:{r.short_trades})')
    print(f'  Win Rate     : {r.win_rate}% (L:{r.long_winrate}% S:{r.short_winrate}%)')
    print(f'  PnL Total    : ${r.total_pnl_dollars:+,.0f}')
    print(f'  Profit Factor: {r.profit_factor}')
    print(f'  Sharpe Ratio : {r.sharpe_ratio}')
    print(f'  Max Drawdown : ${r.max_drawdown:,.0f}')
    print(f'  Expectancy   : ${r.expectancy:.2f}/trade')
    print(f'  Avg Win      : ${r.avg_win_dollars:.0f}  Avg Loss: ${r.avg_loss_dollars:.0f}')
    total = max(r.total_trades, 1)
    print(f'  Exits        : TP={r.exits_tp}({r.exits_tp/total*100:.0f}%) '
          f'SL={r.exits_sl}({r.exits_sl/total*100:.0f}%) '
          f'Time={r.exits_time}({r.exits_time/total*100:.0f}%)')
    print(f'  Jours trades : {r.days_traded} (profit:{r.days_profitable} perte:{r.days_losing})')
    print(f'  Best day     : ${r.best_day:+,.0f}  Worst day: ${r.worst_day:+,.0f}')
    print(f'  J > -$1,800  : {j_agent}')
    print(f'  J > -$4,500  : {j_topstep}')
    print(f'  Proj/mois    : ${r.projected_monthly:+,.0f}')
    print()

    # Critères
    dd_ok = r.max_drawdown < 4500
    pf_ok = r.profit_factor > 1.3
    proj_ok = r.projected_monthly > 7000
    jt_ok = j_topstep == 0
    passes = dd_ok and pf_ok and proj_ok and jt_ok

    print(f'  DD < $4,500  : {"OUI" if dd_ok else "NON"} (${r.max_drawdown:,.0f})')
    print(f'  PF > 1.3     : {"OUI" if pf_ok else "NON"} ({r.profit_factor})')
    print(f'  Proj > $7,000: {"OUI" if proj_ok else "NON"} (${r.projected_monthly:,.0f})')
    print(f'  J > $4,500   : {"OUI" if jt_ok else "NON"} ({j_topstep})')
    print(f'  VERDICT      : {"PASSE" if passes else "NE PASSE PAS"}')
    print()

    # P&L jour par jour
    trades = r.trades
    print('P&L JOUR PAR JOUR')
    print('-' * 95)
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
        bar = '#' * min(int(abs(pnl) / 200), 20)
        flag = ''
        if pnl < TOPSTEP_LIMIT:
            flag = ' !! TOPSTEP'
        elif pnl < AGENT_LIMIT:
            flag = ' ! >AGENT'
        print(
            f'  {day} : {sign}${abs(pnl):>7,.0f} {bar:20s} '
            f'eq=${equity:>+9,.0f}  [{dirs.strip()}]{flag}'
        )

    print()
    print(f'Equity finale : ${equity:+,.0f}')

    # Sauvegarde
    save = {
        'config': 'VIDEO optimisee MNQ x2',
        'params': params,
        'daily_pnl': r.daily_pnl,
        'total_trades': r.total_trades,
        'win_rate': r.win_rate,
        'total_pnl_dollars': r.total_pnl_dollars,
        'profit_factor': r.profit_factor,
        'max_drawdown': r.max_drawdown,
        'sharpe_ratio': r.sharpe_ratio,
        'projected_monthly': r.projected_monthly,
        'trades': trades,
    }
    Path('data').mkdir(exist_ok=True)
    Path('data/opr_mnq_video.json').write_text(json.dumps(save, default=str))
    print('Sauvegarde -> data/opr_mnq_video.json')

    await client.logout()


asyncio.run(run())
