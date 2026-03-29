"""
Backtest DALTON HYBRID — Dalton signals + OPR VIDEO money management.
Dalton: IB, Open Type, Day Type → entry direction
OPR VIDEO MM: TP long 252, SL long 273, TP short 198, SL short 116
Flat 21h44, MNQ x4 ($8/pt), daily_loss_limit -$1800
"""
import asyncio
import sys
import json
import os

sys.path.insert(0, '.')
from dotenv import load_dotenv
load_dotenv()

AGENT_LIMIT = -1800
TOPSTEP_LIMIT = -4500
POINT_VALUE = 2.0
CONTRACTS = 4
DOLLAR_PER_PT = POINT_VALUE * CONTRACTS  # $8/pt

# OPR VIDEO fixed TP/SL (in points)
TP_LONG = 252
SL_LONG = 273
TP_SHORT = 198
SL_SHORT = 116

CLOSE_HOUR = 21
CLOSE_MIN = 44
MAX_TRADES = 9
MAX_LONGS = 5
MAX_SHORTS = 4


async def run():
    from projectx_api import ProjectXClient, ConnectionURLS, AggregationUnit
    from backtester.dalton_engine import DaltonEngine, DayContext
    import pandas as pd
    import numpy as np
    from datetime import datetime, timedelta
    from pathlib import Path
    import pytz

    PARIS_TZ = pytz.timezone('Europe/Paris')

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

    # === DALTON ENGINE (for signal generation only) ===
    dalton = DaltonEngine(params={
        'point_value': POINT_VALUE,
        'contracts': CONTRACTS,
        'close_hour': CLOSE_HOUR,
        'close_min': CLOSE_MIN,
    })

    # Convert to Paris timezone
    df = df5.copy()
    if df.index.tz is None:
        df.index = df.index.tz_localize('UTC')
    df.index = df.index.tz_convert(PARIS_TZ)

    # Group by day
    daily_groups = {}
    for ts, row in df.iterrows():
        d = ts.date()
        if d not in daily_groups:
            daily_groups[d] = []
        daily_groups[d].append((ts, row))

    sorted_dates = sorted(daily_groups.keys())
    ib_ranges_history = []
    all_trades = []
    equity = 0.0
    equity_curve = [0.0]
    daily_pnl_dict = {}
    prev_va = {}

    for trade_date in sorted_dates:
        bars_of_day = daily_groups[trade_date]
        if len(bars_of_day) < 10:
            continue

        ctx = DayContext()
        ctx.date = str(trade_date)
        ctx.prev_poc = prev_va.get('poc', 0)
        ctx.prev_vah = prev_va.get('vah', 0)
        ctx.prev_val = prev_va.get('val', 0)
        ctx.ib_avg_range = np.mean(ib_ranges_history[-10:]) if ib_ranges_history else 0

        daily_pnl = 0.0
        trades_today = 0
        longs_today = 0
        shorts_today = 0

        in_trade = False
        direction = ''
        entry_price = 0.0
        stop_price = 0.0
        target_price = 0.0
        entry_time = None
        signal_type = ''
        bars_held = 0

        for bar_idx, (ts, bar) in enumerate(bars_of_day):
            hour = ts.hour
            minute = ts.minute

            if hour < 9 or hour >= 22:
                continue

            # === IB CALCULATION (15h30-16h30) ===
            if (hour == 15 and minute >= 30) or (hour == 16 and minute < 30):
                if not ctx.ib_complete:
                    if hour == 15 and minute == 30:
                        ctx.open_price = bar['open']
                    if ctx.ib_high == 0:
                        ctx.ib_high = bar['high']
                        ctx.ib_low = bar['low']
                    else:
                        ctx.ib_high = max(ctx.ib_high, bar['high'])
                        ctx.ib_low = min(ctx.ib_low, bar['low'])

            # IB complete at 16h30
            if hour == 16 and minute >= 30 and not ctx.ib_complete and ctx.ib_high > 0:
                ctx.ib_range = ctx.ib_high - ctx.ib_low
                ctx.ib_complete = True
                ib_ranges_history.append(ctx.ib_range)
                ib_bars = pd.DataFrame([r for t, r in bars_of_day if t.hour == 15 and t.minute >= 30])
                if len(ib_bars) > 0:
                    ctx.open_type = dalton.classify_open_type(
                        ctx.open_price, ctx.prev_vah, ctx.prev_val, ctx.prev_poc, ib_bars)

            if not ctx.ib_complete:
                continue

            # === DAY TYPE at 17h00 ===
            if not ctx.day_type_set and hour == 17 and minute == 0:
                bars_so_far = pd.DataFrame([r for t, r in bars_of_day if t <= ts])
                ctx.day_type = dalton.classify_day_type(ctx, bars_so_far)
                ctx.day_type_set = True

            if ctx.day_type in ['nontrend']:
                continue

            # Flat filter
            force_flat = (hour > CLOSE_HOUR or (hour == CLOSE_HOUR and minute >= CLOSE_MIN))

            # Daily loss limit check
            if daily_pnl <= AGENT_LIMIT:
                if in_trade:
                    pnl_pts = (bar['close'] - entry_price) if direction == 'long' else (entry_price - bar['close'])
                    pnl_usd = pnl_pts * DOLLAR_PER_PT
                    equity += pnl_usd
                    daily_pnl += pnl_usd
                    all_trades.append({
                        'entry_time': str(entry_time), 'exit_time': str(ts),
                        'direction': direction, 'entry_price': round(entry_price, 2),
                        'exit_price': round(bar['close'], 2), 'pnl_points': round(pnl_pts, 2),
                        'pnl_dollars': round(pnl_usd, 2), 'exit_reason': 'daily_limit',
                        'signal_type': signal_type, 'day_type': ctx.day_type, 'open_type': ctx.open_type,
                    })
                    in_trade = False
                    equity_curve.append(equity)
                continue

            if trades_today >= MAX_TRADES and not in_trade:
                continue

            # === TRADE MANAGEMENT (OPR VIDEO MM) ===
            if in_trade:
                bars_held += 1

                if bar['high'] > ctx.ib_high:
                    ctx.extended_up = True
                if bar['low'] < ctx.ib_low:
                    ctx.extended_down = True

                # TP hit
                tp_hit = False
                if direction == 'long' and bar['high'] >= target_price:
                    tp_hit = True
                    exit_price = target_price
                elif direction == 'short' and bar['low'] <= target_price:
                    tp_hit = True
                    exit_price = target_price

                if tp_hit:
                    pnl_pts = (exit_price - entry_price) if direction == 'long' else (entry_price - exit_price)
                    pnl_usd = pnl_pts * DOLLAR_PER_PT
                    equity += pnl_usd
                    daily_pnl += pnl_usd
                    all_trades.append({
                        'entry_time': str(entry_time), 'exit_time': str(ts),
                        'direction': direction, 'entry_price': round(entry_price, 2),
                        'exit_price': round(exit_price, 2), 'pnl_points': round(pnl_pts, 2),
                        'pnl_dollars': round(pnl_usd, 2), 'exit_reason': 'tp',
                        'signal_type': signal_type, 'day_type': ctx.day_type, 'open_type': ctx.open_type,
                    })
                    trades_today += 1
                    in_trade = False
                    equity_curve.append(equity)
                    continue

                # SL hit
                sl_hit = False
                if direction == 'long' and bar['low'] <= stop_price:
                    sl_hit = True
                    exit_price = stop_price
                elif direction == 'short' and bar['high'] >= stop_price:
                    sl_hit = True
                    exit_price = stop_price

                if sl_hit:
                    pnl_pts = (exit_price - entry_price) if direction == 'long' else (entry_price - exit_price)
                    pnl_usd = pnl_pts * DOLLAR_PER_PT
                    equity += pnl_usd
                    daily_pnl += pnl_usd
                    all_trades.append({
                        'entry_time': str(entry_time), 'exit_time': str(ts),
                        'direction': direction, 'entry_price': round(entry_price, 2),
                        'exit_price': round(exit_price, 2), 'pnl_points': round(pnl_pts, 2),
                        'pnl_dollars': round(pnl_usd, 2), 'exit_reason': 'sl',
                        'signal_type': signal_type, 'day_type': ctx.day_type, 'open_type': ctx.open_type,
                    })
                    trades_today += 1
                    in_trade = False
                    equity_curve.append(equity)
                    continue

                # FLAT FILTER — close at 21h44
                if force_flat:
                    pnl_pts = (bar['close'] - entry_price) if direction == 'long' else (entry_price - bar['close'])
                    pnl_usd = pnl_pts * DOLLAR_PER_PT
                    equity += pnl_usd
                    daily_pnl += pnl_usd
                    all_trades.append({
                        'entry_time': str(entry_time), 'exit_time': str(ts),
                        'direction': direction, 'entry_price': round(entry_price, 2),
                        'exit_price': round(bar['close'], 2), 'pnl_points': round(pnl_pts, 2),
                        'pnl_dollars': round(pnl_usd, 2), 'exit_reason': 'flat',
                        'signal_type': signal_type, 'day_type': ctx.day_type, 'open_type': ctx.open_type,
                    })
                    trades_today += 1
                    in_trade = False
                    equity_curve.append(equity)

            # === SIGNAL SEARCH (Dalton) ===
            else:
                if force_flat:
                    continue
                if bar_idx < 1:
                    continue
                if daily_pnl <= AGENT_LIMIT:
                    continue
                if trades_today >= MAX_TRADES:
                    continue

                prev_bar = bars_of_day[bar_idx - 1][1]
                signal = dalton.get_signal(ctx, bar, prev_bar, hour, minute)
                if signal is None:
                    continue

                sig_dir = signal['direction']

                # Check long/short limits
                if sig_dir == 'long' and longs_today >= MAX_LONGS:
                    continue
                if sig_dir == 'short' and shorts_today >= MAX_SHORTS:
                    continue

                # OPR VIDEO fixed TP/SL
                in_trade = True
                direction = sig_dir
                entry_price = bar['close']
                entry_time = ts
                signal_type = signal['signal']
                bars_held = 0

                if direction == 'long':
                    target_price = entry_price + TP_LONG
                    stop_price = entry_price - SL_LONG
                    longs_today += 1
                else:
                    target_price = entry_price - TP_SHORT
                    stop_price = entry_price + SL_SHORT
                    shorts_today += 1

        # End of day — close open trade
        if in_trade:
            last_ts, last_bar = bars_of_day[-1]
            pnl_pts = (last_bar['close'] - entry_price) if direction == 'long' else (entry_price - last_bar['close'])
            pnl_usd = pnl_pts * DOLLAR_PER_PT
            equity += pnl_usd
            daily_pnl += pnl_usd
            all_trades.append({
                'entry_time': str(entry_time), 'exit_time': str(last_ts),
                'direction': direction, 'entry_price': round(entry_price, 2),
                'exit_price': round(last_bar['close'], 2), 'pnl_points': round(pnl_pts, 2),
                'pnl_dollars': round(pnl_usd, 2), 'exit_reason': 'eod',
                'signal_type': signal_type, 'day_type': ctx.day_type, 'open_type': ctx.open_type,
            })
            in_trade = False
            equity_curve.append(equity)

        # Value area for next day
        day_df = pd.DataFrame([r for t, r in bars_of_day])
        va = dalton.calc_value_area(day_df)
        if va:
            prev_va = va

        if daily_pnl != 0:
            daily_pnl_dict[str(trade_date)] = round(daily_pnl, 2)

    # === RAPPORT ===
    if not all_trades:
        print('Aucun trade')
        await client.logout()
        return

    pnls = [t['pnl_dollars'] for t in all_trades]
    winners = [p for p in pnls if p > 0]
    losers = [p for p in pnls if p <= 0]
    total = len(all_trades)
    win_rate = len(winners) / total * 100 if total else 0
    total_pnl = sum(pnls)
    gross_win = sum(winners) if winners else 0
    gross_loss = abs(sum(losers)) if losers else 0
    pf = gross_win / gross_loss if gross_loss > 0 else float('inf')
    expectancy = np.mean(pnls) if pnls else 0

    # Max drawdown
    peak = 0.0
    max_dd = 0.0
    for e in equity_curve:
        if e > peak:
            peak = e
        dd = peak - e
        if dd > max_dd:
            max_dd = dd

    # Sharpe
    sharpe = 0
    if len(pnls) > 1:
        sharpe = np.mean(pnls) / np.std(pnls) * np.sqrt(252) if np.std(pnls) > 0 else 0

    pnl_vals = list(daily_pnl_dict.values())
    j_agent = sum(1 for p in pnl_vals if p < AGENT_LIMIT)
    j_topstep = sum(1 for p in pnl_vals if p < TOPSTEP_LIMIT)
    days_traded = len(pnl_vals)
    days_profit = sum(1 for p in pnl_vals if p > 0)
    days_loss = sum(1 for p in pnl_vals if p < 0)
    avg_daily = np.mean(pnl_vals) if pnl_vals else 0
    proj_monthly = avg_daily * 20

    longs = [t for t in all_trades if t['direction'] == 'long']
    shorts = [t for t in all_trades if t['direction'] == 'short']
    long_wr = len([t for t in longs if t['pnl_dollars'] > 0]) / len(longs) * 100 if longs else 0
    short_wr = len([t for t in shorts if t['pnl_dollars'] > 0]) / len(shorts) * 100 if shorts else 0

    exits_tp = sum(1 for t in all_trades if t['exit_reason'] == 'tp')
    exits_sl = sum(1 for t in all_trades if t['exit_reason'] == 'sl')
    exits_flat = sum(1 for t in all_trades if t['exit_reason'] in ('flat', 'eod'))
    exits_dl = sum(1 for t in all_trades if t['exit_reason'] == 'daily_limit')

    print()
    print('=' * 85)
    print('BACKTEST DALTON HYBRID — Dalton Signals + OPR VIDEO MM — MNQ x4')
    print('=' * 85)
    print(f'  Instrument  : MNQ (Micro NQ) x {CONTRACTS} contrats')
    print(f'  Point value : ${POINT_VALUE} x {CONTRACTS} = ${DOLLAR_PER_PT}/pt')
    print(f'  TP Long     : {TP_LONG} pts (${TP_LONG * DOLLAR_PER_PT:.0f})')
    print(f'  SL Long     : {SL_LONG} pts (${SL_LONG * DOLLAR_PER_PT:.0f})')
    print(f'  TP Short    : {TP_SHORT} pts (${TP_SHORT * DOLLAR_PER_PT:.0f})')
    print(f'  SL Short    : {SL_SHORT} pts (${SL_SHORT * DOLLAR_PER_PT:.0f})')
    print(f'  Max trades  : {MAX_TRADES} (L:{MAX_LONGS} S:{MAX_SHORTS})')
    print(f'  Flat        : {CLOSE_HOUR}h{CLOSE_MIN:02d}')
    print(f'  Daily limit : ${AGENT_LIMIT}')
    print(f'  Signals     : Dalton (IB > Open Type > Day Type)')
    print('=' * 85)
    print()

    print('RESULTATS')
    print('-' * 50)
    print(f'  Trades       : {total} (L:{len(longs)} S:{len(shorts)})')
    print(f'  Win Rate     : {win_rate:.1f}% (L:{long_wr:.1f}% S:{short_wr:.1f}%)')
    print(f'  PnL Total    : ${total_pnl:+,.0f}')
    print(f'  Profit Factor: {pf:.2f}')
    print(f'  Sharpe Ratio : {sharpe:.2f}')
    print(f'  Max Drawdown : ${max_dd:,.0f}')
    print(f'  Expectancy   : ${expectancy:.2f}/trade')
    print(f'  Avg Win      : ${np.mean(winners):.0f}  Avg Loss: ${abs(np.mean(losers)):.0f}' if winners and losers else '')
    print(f'  Exits        : TP={exits_tp}({exits_tp/max(total,1)*100:.0f}%) '
          f'SL={exits_sl}({exits_sl/max(total,1)*100:.0f}%) '
          f'Flat={exits_flat}({exits_flat/max(total,1)*100:.0f}%) '
          f'DL={exits_dl}({exits_dl/max(total,1)*100:.0f}%)')
    print(f'  Jours trades : {days_traded} (profit:{days_profit} perte:{days_loss})')
    print(f'  Best day     : ${max(pnl_vals):+,.0f}  Worst day: ${min(pnl_vals):+,.0f}' if pnl_vals else '')
    print(f'  J > -$1,800  : {j_agent}')
    print(f'  J > -$4,500  : {j_topstep}')
    print(f'  Proj/mois    : ${proj_monthly:+,.0f}')
    print()

    # Criteres
    dd_ok = max_dd < 4500
    pf_ok = pf > 1.3
    proj_ok = proj_monthly > 7000
    jt_ok = j_topstep == 0
    passes = dd_ok and pf_ok and proj_ok and jt_ok

    print(f'  DD < $4,500  : {"OUI" if dd_ok else "NON"} (${max_dd:,.0f})')
    print(f'  PF > 1.3     : {"OUI" if pf_ok else "NON"} ({pf:.2f})')
    print(f'  Proj > $7,000: {"OUI" if proj_ok else "NON"} (${proj_monthly:,.0f})')
    print(f'  J > $4,500   : {"OUI" if jt_ok else "NON"} ({j_topstep})')
    print(f'  VERDICT      : {"PASSE" if passes else "NE PASSE PAS"}')
    print()

    # Signals breakdown
    print('BREAKDOWN PAR SIGNAL')
    print('-' * 60)
    signal_types = {}
    for t in all_trades:
        s = t['signal_type']
        if s not in signal_types:
            signal_types[s] = {'trades': 0, 'wins': 0, 'pnl': 0}
        signal_types[s]['trades'] += 1
        signal_types[s]['pnl'] += t['pnl_dollars']
        if t['pnl_dollars'] > 0:
            signal_types[s]['wins'] += 1
    for sig, data in sorted(signal_types.items()):
        wr = data['wins'] / data['trades'] * 100 if data['trades'] else 0
        print(f'  {sig:<25} {data["trades"]:>3} trades  WR={wr:>5.1f}%  PnL=${data["pnl"]:>+8,.0f}')
    print()

    # Day type breakdown
    print('BREAKDOWN PAR DAY TYPE')
    print('-' * 60)
    day_types = {}
    for t in all_trades:
        dt = t['day_type']
        if dt not in day_types:
            day_types[dt] = {'trades': 0, 'wins': 0, 'pnl': 0}
        day_types[dt]['trades'] += 1
        day_types[dt]['pnl'] += t['pnl_dollars']
        if t['pnl_dollars'] > 0:
            day_types[dt]['wins'] += 1
    for dt, data in sorted(day_types.items()):
        wr = data['wins'] / data['trades'] * 100 if data['trades'] else 0
        print(f'  {dt:<25} {data["trades"]:>3} trades  WR={wr:>5.1f}%  PnL=${data["pnl"]:>+8,.0f}')
    print()

    # P&L jour par jour
    print('P&L JOUR PAR JOUR')
    print('-' * 95)
    eq = 0
    for day, pnl in sorted(daily_pnl_dict.items()):
        eq += pnl
        day_trades = [t for t in all_trades if t['entry_time'].startswith(day)]
        dirs = ''
        for t in day_trades:
            d_char = 'L' if t['direction'] == 'long' else 'S'
            ex = t['exit_reason'][:2].upper()
            dirs += d_char + ':' + ex + ' '
        sign = '+' if pnl >= 0 else '-'
        bar_chart = '#' * min(int(abs(pnl) / 200), 20)
        flag = ''
        if pnl < TOPSTEP_LIMIT:
            flag = ' !! TOPSTEP'
        elif pnl < AGENT_LIMIT:
            flag = ' ! >AGENT'
        print(
            f'  {day} : {sign}${abs(pnl):>7,.0f} {bar_chart:20s} '
            f'eq=${eq:>+9,.0f}  [{dirs.strip()}]{flag}'
        )

    print()
    print(f'Equity finale : ${eq:+,.0f}')

    # === COMPARAISON avec OPR VIDEO ===
    print()
    print('=' * 70)
    print('COMPARAISON — DALTON HYBRID vs OPR VIDEO (production)')
    print('=' * 70)
    opr = json.loads(Path('data/config_production.json').read_text())['backtest']
    print(f'  {"Metrique":<25} {"OPR VIDEO":>15} {"DALTON HYBRID":>15}')
    print('-' * 70)
    print(f'  {"Trades":<25} {opr["trades"]:>15} {total:>15}')
    print(f'  {"Win Rate":<25} {opr["win_rate"]:>14.1f}% {win_rate:>14.1f}%')
    print(f'  {"PnL Total":<25} {"$"+str(int(opr["pnl_total"])):>15} {"$"+str(int(total_pnl)):>15}')
    print(f'  {"Profit Factor":<25} {opr["profit_factor"]:>15.2f} {pf:>15.2f}')
    print(f'  {"Sharpe Ratio":<25} {opr["sharpe_ratio"]:>15.2f} {sharpe:>15.2f}')
    print(f'  {"Max Drawdown":<25} {"$"+str(int(opr["max_drawdown"])):>15} {"$"+str(int(max_dd)):>15}')
    print(f'  {"J > -$1,800":<25} {opr["days_over_agent"]:>15} {j_agent:>15}')
    print(f'  {"J > -$4,500":<25} {opr["days_over_topstep"]:>15} {j_topstep:>15}')
    print(f'  {"Proj/mois":<25} {"$"+str(int(opr["projected_monthly_20j"])):>15} {"$"+str(int(proj_monthly)):>15}')
    print()

    # Sauvegarde
    save = {
        'config': 'DALTON HYBRID — Dalton signals + OPR VIDEO MM — MNQ x4',
        'params': {
            'tp_long': TP_LONG, 'sl_long': SL_LONG,
            'tp_short': TP_SHORT, 'sl_short': SL_SHORT,
            'max_trades': MAX_TRADES, 'max_longs': MAX_LONGS, 'max_shorts': MAX_SHORTS,
            'close_hour': CLOSE_HOUR, 'close_min': CLOSE_MIN,
            'point_value': POINT_VALUE, 'contracts': CONTRACTS,
            'daily_loss_limit': AGENT_LIMIT,
            'signal_source': 'dalton_market_profile',
        },
        'results': {
            'trades': total, 'win_rate': round(win_rate, 1),
            'pnl_total': round(total_pnl, 2), 'profit_factor': round(pf, 2),
            'sharpe_ratio': round(sharpe, 2), 'max_drawdown': round(max_dd, 2),
            'projected_monthly': round(proj_monthly, 2),
            'days_over_agent': j_agent, 'days_over_topstep': j_topstep,
        },
        'daily_pnl': daily_pnl_dict,
        'trades': all_trades,
    }
    Path('data').mkdir(exist_ok=True)
    Path('data/dalton_hybrid_results.json').write_text(json.dumps(save, default=str))
    print('Sauvegarde -> data/dalton_hybrid_results.json')

    await client.logout()


asyncio.run(run())
