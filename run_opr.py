"""Backtest OPR 150K — 3 variantes sur 120 jours — Topstep $150k."""
import asyncio
import sys
import json
import os

sys.path.insert(0, '.')
from dotenv import load_dotenv
load_dotenv()

# Topstep $150k
TOPSTEP_150K = {
    'daily_loss_topstep': -4500,
    'trailing_dd': -4500,
    'daily_loss_agent': -1800,  # 40% de 4500
}


async def run():
    from projectx_api import ProjectXClient, ConnectionURLS, AggregationUnit
    from backtester.opr_engine import OPREngine
    import pandas as pd
    from datetime import datetime, timedelta
    from pathlib import Path

    DAYS = 120
    AGENT_LIMIT = TOPSTEP_150K['daily_loss_agent']
    TOPSTEP_LIMIT = TOPSTEP_150K['daily_loss_topstep']

    TOPSTEPX_URLS = ConnectionURLS(
        api_endpoint='https://api.topstepx.com',
        user_hub='https://rtc.topstepx.com/hubs/user',
        market_hub='https://rtc.topstepx.com/hubs/market',
    )

    print('=' * 85)
    print('BACKTEST OPR 150K — 3 VARIANTES — 120 JOURS')
    print(f'Compte         : Topstep $150k')
    print(f'Daily limit    : ${TOPSTEP_LIMIT} (Topstep) / ${AGENT_LIMIT} (agent)')
    print(f'Trailing DD    : ${TOPSTEP_150K["trailing_dd"]}')
    print('=' * 85)

    print('Connexion ProjectX...')
    client = ProjectXClient(TOPSTEPX_URLS)
    await client.login({
        'auth_type': 'api_key',
        'userName': os.getenv('PROJECTX_USERNAME'),
        'apiKey': os.getenv('PROJECTX_API_KEY'),
    })

    contracts = await client.search_for_contracts(searchText='NQ', live=False)
    contract_id = contracts[0]['id']
    print(f'Connecte - NQ id={contract_id}')

    # Fetch 5min data une seule fois
    now = datetime.utcnow()
    all_5min = []
    n_chunks = (DAYS // 7) + 1

    print('Recuperation barres 5min...')
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
        except Exception as e:
            print(f'  chunk {chunk_i} err: {e}')

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

    # === 3 VARIANTES 150K ===
    variantes = {
        'OPR VIDEO (adapte)': {
            'tp_long': 100, 'sl_long': 80,
            'tp_short': 80, 'sl_short': 50,
            'max_trades': 9,
            'max_longs': 5, 'max_shorts': 4,
            'min_range': 15, 'max_range': 999,
        },
        'OPR 150K EQUILIBREE': {
            'tp_long': 80, 'sl_long': 60,
            'tp_short': 60, 'sl_short': 45,
            'max_trades': 5,
            'max_longs': 3, 'max_shorts': 2,
            'min_range': 20, 'max_range': 300,
        },
        'OPR 150K CONSERVATIVE': {
            'tp_long': 60, 'sl_long': 45,
            'tp_short': 50, 'sl_short': 35,
            'max_trades': 3,
            'max_longs': 2, 'max_shorts': 1,
            'min_range': 20, 'max_range': 250,
        },
    }

    # === TABLEAU COMPARATIF ===
    print('=' * 105)
    print('COMPARAISON 3 VARIANTES — TOPSTEP $150K')
    print('=' * 105)
    print(
        f'{"Variante":<25} '
        f'{"Trades":>7} '
        f'{"WR":>6} '
        f'{"PnL":>9} '
        f'{"PF":>5} '
        f'{"Sharpe":>7} '
        f'{"DD":>8} '
        f'{"J>1800":>7} '
        f'{"J>4500":>7} '
        f'{"Proj/m":>8} '
        f'{"OK":>3}'
    )
    print('-' * 105)

    resultats = {}
    for nom, params in variantes.items():
        engine = OPREngine(params=params)
        r = engine.run(df5, daily_loss_limit=AGENT_LIMIT, max_trades_per_day=params['max_trades'])
        if r:
            # Recalcul jours > limites 150k
            pnl_vals = list(r.daily_pnl.values())
            j_agent = sum(1 for p in pnl_vals if p < AGENT_LIMIT)
            j_topstep = sum(1 for p in pnl_vals if p < TOPSTEP_LIMIT)

            dd_ok = 'Y' if r.max_drawdown < 4500 and j_topstep == 0 and r.profit_factor >= 1.3 else 'N'
            print(
                f'{nom:<25} '
                f'{r.total_trades:>7} '
                f'{r.win_rate:>5.1f}% '
                f'${r.total_pnl_dollars:>8.0f} '
                f'{r.profit_factor:>5.2f} '
                f'{r.sharpe_ratio:>7.2f} '
                f'${r.max_drawdown:>7.0f} '
                f'{j_agent:>7} '
                f'{j_topstep:>7} '
                f'${r.projected_monthly:>7.0f} '
                f'{dd_ok:>3}'
            )
            resultats[nom] = {
                'report': r,
                'j_agent': j_agent,
                'j_topstep': j_topstep,
            }
        else:
            print(f'{nom:<25} aucun trade')

    # === DETAIL DE CHAQUE VARIANTE ===
    for nom, data in resultats.items():
        r = data['report']
        print()
        print('=' * 85)
        print(f'DETAIL : {nom}')
        p = variantes[nom]
        print(f'  TP_L={p["tp_long"]} SL_L={p["sl_long"]} TP_S={p["tp_short"]} SL_S={p["sl_short"]} '
              f'maxT={p["max_trades"]} maxL={p["max_longs"]} maxS={p["max_shorts"]} '
              f'rng={p["min_range"]}-{p["max_range"]}')
        print('=' * 85)
        print(f'  Trades     : {r.total_trades} (L:{r.long_trades} S:{r.short_trades})')
        print(f'  Win Rate   : {r.win_rate}% (L:{r.long_winrate}% S:{r.short_winrate}%)')
        print(f'  P&L        : ${r.total_pnl_dollars:+.0f}')
        print(f'  PF         : {r.profit_factor}')
        print(f'  Sharpe     : {r.sharpe_ratio}')
        print(f'  Max DD     : ${r.max_drawdown:.0f}')
        print(f'  Expectancy : ${r.expectancy:.2f}/trade')
        print(f'  Avg Win    : ${r.avg_win_dollars:.0f}  Avg Loss: ${r.avg_loss_dollars:.0f}')
        total = max(r.total_trades, 1)
        print(f'  Exits      : TP={r.exits_tp}({r.exits_tp/total*100:.0f}%) '
              f'SL={r.exits_sl}({r.exits_sl/total*100:.0f}%) '
              f'Time={r.exits_time}({r.exits_time/total*100:.0f}%)')
        print(f'  Jours      : {r.days_traded} trades / {r.days_profitable} profit / {r.days_losing} perte')
        print(f'  Best day   : ${r.best_day:+.0f}  Worst day: ${r.worst_day:+.0f}')
        print(f'  J > -$1800 : {data["j_agent"]}   J > -$4500 : {data["j_topstep"]}')
        print(f'  Proj/mois  : ${r.projected_monthly:+.0f}')

    # === MEILLEURE VARIANTE ===
    print()
    print('=' * 85)

    best = None
    best_score = 0
    for nom, data in resultats.items():
        r = data['report']
        passes = r.max_drawdown < 4500 and data['j_topstep'] == 0 and r.profit_factor >= 1.3
        score = r.profit_factor * r.projected_monthly / 10000 if passes else 0
        if score > best_score:
            best_score = score
            best = (nom, data)

    # Si aucune passe les 3 criteres, prendre la meilleure quand meme
    if not best:
        for nom, data in resultats.items():
            r = data['report']
            score = r.profit_factor * max(r.projected_monthly, 0) / 10000
            if score > best_score:
                best_score = score
                best = (nom, data)

    if best:
        nom, data = best
        r = data['report']

        dd_check = 'Y' if r.max_drawdown < 4500 else 'N'
        j_check = 'Y' if data['j_topstep'] == 0 else 'N'
        pf_check = 'Y' if r.profit_factor >= 1.3 else 'N'

        print(f'MEILLEURE VARIANTE : {nom}')
        print('=' * 85)
        print(f'  PF         : {r.profit_factor}   {"< 1.3" if pf_check == "N" else ">= 1.3"} {pf_check}')
        print(f'  WR         : {r.win_rate}%')
        print(f'  P&L 120j   : ${r.total_pnl_dollars:+.0f}')
        print(f'  Max DD     : ${r.max_drawdown:.0f}   {"< $4,500" if dd_check == "Y" else "> $4,500"} {dd_check}')
        print(f'  Sharpe     : {r.sharpe_ratio}')
        print(f'  Proj/mois  : ${r.projected_monthly:+.0f}')
        print(f'  Trades     : {r.total_trades} (L:{r.long_trades} S:{r.short_trades})')
        print(f'  Long WR    : {r.long_winrate}%')
        print(f'  Short WR   : {r.short_winrate}%')
        print(f'  J > -$1800 : {data["j_agent"]}')
        print(f'  J > -$4500 : {data["j_topstep"]}   {j_check}')
        print()

        # VERDICT
        all_pass = dd_check == 'Y' and j_check == 'Y' and pf_check == 'Y'
        if all_pass and r.profit_factor >= 1.5:
            print('VERDICT: EXCELLENT — Tous criteres passes, PF >= 1.5')
        elif all_pass:
            print('VERDICT: BON — Tous criteres passes (DD<4500, 0 j>4500, PF>=1.3)')
        elif r.profit_factor >= 1.3:
            print(f'VERDICT: A AFFINER — PF OK mais DD=${r.max_drawdown:.0f} ou J>4500={data["j_topstep"]}')
        else:
            print(f'VERDICT: INSUFFISANT — PF={r.profit_factor} DD=${r.max_drawdown:.0f}')

        # P&L jour par jour
        print()
        print('  P&L par jour :')
        for day, pnl in sorted(r.daily_pnl.items()):
            bar_len = int(abs(pnl) / 200)
            bar = '#' * min(bar_len, 15)
            sign = '+' if pnl >= 0 else '-'
            flag = ''
            if pnl < TOPSTEP_LIMIT:
                flag = ' !! >LIMITE TOPSTEP'
            elif pnl < AGENT_LIMIT:
                flag = ' ! >LIMITE AGENT'
            print(f'    {day} : {sign}${abs(pnl):7.0f} {bar}{flag}')

    # Sauvegarde
    save = {}
    for nom, data in resultats.items():
        r = data['report']
        save[nom] = {
            'params': variantes[nom],
            'total_trades': r.total_trades,
            'win_rate': r.win_rate,
            'total_pnl': r.total_pnl_dollars,
            'profit_factor': r.profit_factor,
            'max_drawdown': r.max_drawdown,
            'sharpe': r.sharpe_ratio,
            'projected_monthly': r.projected_monthly,
            'long_trades': r.long_trades,
            'long_winrate': r.long_winrate,
            'short_trades': r.short_trades,
            'short_winrate': r.short_winrate,
            'days_over_agent': data['j_agent'],
            'days_over_topstep': data['j_topstep'],
        }
    Path('data').mkdir(exist_ok=True)
    Path('data/opr_150k_3variantes.json').write_text(json.dumps(save, default=str))
    print()
    print('Sauvegarde -> data/opr_150k_3variantes.json')

    await client.logout()
    print('\nTermine.')


asyncio.run(run())
