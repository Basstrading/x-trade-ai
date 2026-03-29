"""Backtest OPR + SuperTrend + SL dynamique PeriodsHighLow — MNQ x4."""
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
    import numpy as np
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
        'tp_long': 217.75,
        'tp_short': 205.75,
        'sl_type': 'periods_high_low',
        'sl_long_periods': 9,
        'sl_long_delta': -41.75,
        'sl_short_periods': 15,
        'sl_short_delta': 0.25,
        'sl_max_pts': 200,
        'supertrend_period': 0,
        'supertrend_mult': 3.4,
        'max_trades': 6,
        'max_longs': 3,
        'max_shorts': 3,
        'min_range': 15,
        'max_range': 999,
        'close_hour': 20,
        'close_min': 49,
        'point_value': 2.0,
        'contracts': 4,
        'daily_loss_limit': AGENT_LIMIT,
    }

    engine = OPREngine(params=params)
    r = engine.run(df5, daily_loss_limit=AGENT_LIMIT, max_trades_per_day=6)

    if not r:
        print('Aucun trade')
        await client.logout()
        return

    pnl_vals = list(r.daily_pnl.values())
    j_agent = sum(1 for p in pnl_vals if p < AGENT_LIMIT)
    j_topstep = sum(1 for p in pnl_vals if p < TOPSTEP_LIMIT)

    p = params
    print('=' * 85)
    print('BACKTEST OPR + SUPERTREND + SL DYNAMIQUE — MNQ x4')
    print('=' * 85)
    print(f'  Instrument  : MNQ (Micro NQ) x {p["contracts"]} contrats')
    print(f'  Point value : ${p["point_value"]} x {p["contracts"]} = ${p["point_value"] * p["contracts"]}/pt')
    print(f'  TP Long     : {p["tp_long"]} pts (${p["tp_long"] * p["point_value"] * p["contracts"]:.0f})')
    print(f'  TP Short    : {p["tp_short"]} pts (${p["tp_short"] * p["point_value"] * p["contracts"]:.0f})')
    print(f'  SL Long     : dynamique (9 derniers low - 41.75 pts)')
    print(f'  SL Short    : dynamique (15 derniers high + 0.25 pts)')
    print(f'  SuperTrend  : period={p["supertrend_period"]}, mult={p["supertrend_mult"]}')
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

    # SL stats
    sl_pts_list = [t['sl_pts'] for t in r.trades]
    print('SL DYNAMIQUE STATS')
    print('-' * 50)
    print(f'  SL moyen     : {np.mean(sl_pts_list):.1f} pts (${np.mean(sl_pts_list) * p["point_value"] * p["contracts"]:.0f})')
    print(f'  SL min       : {min(sl_pts_list):.1f} pts')
    print(f'  SL max       : {max(sl_pts_list):.1f} pts')
    print(f'  SL median    : {np.median(sl_pts_list):.1f} pts')
    print()

    # Criteres
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

    # Comparaison avec OPR VIDEO production
    opr = json.loads(Path('data/config_production.json').read_text())['backtest']
    print('=' * 70)
    print('COMPARAISON — SuperTrend+DynSL vs OPR VIDEO (production)')
    print('=' * 70)
    print(f'  {"Metrique":<25} {"OPR VIDEO":>15} {"ST+DynSL":>15}')
    print('-' * 70)
    print(f'  {"Trades":<25} {opr["trades"]:>15} {r.total_trades:>15}')
    print(f'  {"Win Rate":<25} {opr["win_rate"]:>14.1f}% {r.win_rate:>14.1f}%')
    print(f'  {"PnL Total":<25} {"$"+str(int(opr["pnl_total"])):>15} {"$"+str(int(r.total_pnl_dollars)):>15}')
    print(f'  {"Profit Factor":<25} {opr["profit_factor"]:>15.2f} {r.profit_factor:>15.2f}')
    print(f'  {"Sharpe Ratio":<25} {opr["sharpe_ratio"]:>15.2f} {r.sharpe_ratio:>15.2f}')
    print(f'  {"Max Drawdown":<25} {"$"+str(int(opr["max_drawdown"])):>15} {"$"+str(int(r.max_drawdown)):>15}')
    print(f'  {"J > -$1,800":<25} {opr["days_over_agent"]:>15} {j_agent:>15}')
    print(f'  {"J > -$4,500":<25} {opr["days_over_topstep"]:>15} {j_topstep:>15}')
    print(f'  {"Proj/mois":<25} {"$"+str(int(opr["projected_monthly_20j"])):>15} {"$"+str(int(r.projected_monthly)):>15}')
    print()

    # === ANALYSE TRADES FILTRES PAR SUPERTREND ===
    filtered = engine.filtered_trades
    print('=' * 95)
    print(f'ANALYSE DES {len(filtered)} TRADES FILTRES PAR SUPERTREND')
    print('=' * 95)
    print()

    if filtered:
        f_winners = [t for t in filtered if t['pnl_dollars'] > 0]
        f_losers = [t for t in filtered if t['pnl_dollars'] <= 0]
        f_pnls = [t['pnl_dollars'] for t in filtered]
        f_total_pnl = sum(f_pnls)
        f_tp = [t for t in filtered if t['exit_reason'] == 'tp']
        f_sl = [t for t in filtered if t['exit_reason'] == 'sl']
        f_te = [t for t in filtered if t['exit_reason'] == 'time_exit']
        f_longs = [t for t in filtered if t['direction'] == 'long']
        f_shorts = [t for t in filtered if t['direction'] == 'short']

        print(f'  Total filtres : {len(filtered)} (L:{len(f_longs)} bloques car ST baissier, '
              f'S:{len(f_shorts)} bloques car ST haussier)')
        print(f'  Auraient GAGNE  : {len(f_winners)} ({len(f_winners)/len(filtered)*100:.0f}%)')
        print(f'  Auraient PERDU  : {len(f_losers)} ({len(f_losers)/len(filtered)*100:.0f}%)')
        print(f'  PnL total simule: ${f_total_pnl:+,.0f}')
        print(f'  PnL moyen/trade : ${np.mean(f_pnls):+,.0f}')
        print(f'  Exits simules   : TP={len(f_tp)} SL={len(f_sl)} Time={len(f_te)}')
        if f_winners:
            print(f'  Avg win simule  : ${np.mean([t["pnl_dollars"] for t in f_winners]):+,.0f}')
        if f_losers:
            print(f'  Avg loss simule : ${np.mean([t["pnl_dollars"] for t in f_losers]):+,.0f}')
        print()

        # Detail par trade filtre
        print('DETAIL DES TRADES FILTRES (simulation)')
        print('-' * 115)
        print(f'  {"Date":<20} {"Dir":>5} {"ST":>8} {"Entry":>10} {"TP":>10} {"SL":>10} '
              f'{"SL pts":>7} {"Exit":>10} {"Reason":>8} {"PnL $":>9}')
        print('-' * 115)
        for t in filtered:
            date_str = t['entry_time'][:16]
            win_mark = '+' if t['pnl_dollars'] > 0 else '-' if t['pnl_dollars'] < 0 else '='
            print(f'  {date_str:<20} {t["direction"]:>5} {t["st_direction"]:>8} '
                  f'{t["entry_price"]:>10.2f} {t["tp_price"]:>10.2f} {t["sl_price"]:>10.2f} '
                  f'{t["sl_pts"]:>7.1f} {t["exit_price"]:>10.2f} {t["exit_reason"]:>8} '
                  f'{win_mark}${abs(t["pnl_dollars"]):>7,.0f}')
        print()

        # Breakdown: longs filtres vs shorts filtres
        print('BREAKDOWN LONG vs SHORT filtres')
        print('-' * 60)
        for label, subset in [('LONG (bloques car ST baissier)', f_longs),
                               ('SHORT (bloques car ST haussier)', f_shorts)]:
            if not subset:
                continue
            sw = [t for t in subset if t['pnl_dollars'] > 0]
            sl_sub = [t for t in subset if t['pnl_dollars'] <= 0]
            spnl = sum(t['pnl_dollars'] for t in subset)
            print(f'  {label}:')
            print(f'    Trades: {len(subset)}  Gagnes: {len(sw)}  Perdus: {len(sl_sub)}  '
                  f'WR: {len(sw)/len(subset)*100:.0f}%  PnL: ${spnl:+,.0f}')

        # Breakdown par exit reason
        print()
        print('BREAKDOWN PAR EXIT REASON simule')
        print('-' * 60)
        for reason, subset in [('TP', f_tp), ('SL', f_sl), ('Time Exit', f_te)]:
            if not subset:
                continue
            spnl = sum(t['pnl_dollars'] for t in subset)
            print(f'  {reason:<12}: {len(subset):>3} trades  PnL ${spnl:>+8,.0f}')

        print()
        # VERDICT
        print('=' * 60)
        if f_total_pnl < 0:
            print(f'VERDICT: Le SuperTrend filtre BIEN !')
            print(f'  Les {len(filtered)} trades bloques auraient genere ${f_total_pnl:+,.0f}')
            print(f'  => Le filtre evite ${abs(f_total_pnl):,.0f} de pertes')
        elif f_total_pnl > 0:
            pct_of_main = f_total_pnl / max(r.total_pnl_dollars, 1) * 100
            print(f'VERDICT: Le SuperTrend filtre TROP !')
            print(f'  Les {len(filtered)} trades bloques auraient genere ${f_total_pnl:+,.0f}')
            print(f'  => Le filtre coute {pct_of_main:.0f}% du PnL actuel')
            combined = r.total_pnl_dollars + f_total_pnl
            print(f'  Sans filtre: ${combined:+,.0f} vs Avec filtre: ${r.total_pnl_dollars:+,.0f}')
        else:
            print(f'VERDICT: Neutre')
        print('=' * 60)
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
            sl_info = f'{t["sl_pts"]:.0f}' if t['sl_pts'] else ''
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
        'config': 'OPR + SuperTrend + SL dynamique — MNQ x4',
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
    Path('data/opr_supertrend_results.json').write_text(json.dumps(save, default=str))
    print('Sauvegarde -> data/opr_supertrend_results.json')

    await client.logout()


asyncio.run(run())
