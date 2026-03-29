"""Diagnostic: find optimal stop/trail values based on real NQ volatility."""
import asyncio
import sys
import os
import numpy as np

sys.path.insert(0, '.')
from dotenv import load_dotenv
load_dotenv()


async def run():
    from projectx_api import ProjectXClient, ConnectionURLS, AggregationUnit
    import pandas as pd
    from datetime import datetime, timedelta

    DAYS = 120

    TOPSTEPX_URLS = ConnectionURLS(
        api_endpoint='https://api.topstepx.com',
        user_hub='https://rtc.topstepx.com/hubs/user',
        market_hub='https://rtc.topstepx.com/hubs/market',
    )

    print('Connexion...')
    client = ProjectXClient(TOPSTEPX_URLS)
    await client.login({
        'auth_type': 'api_key',
        'userName': os.getenv('PROJECTX_USERNAME'),
        'apiKey': os.getenv('PROJECTX_API_KEY'),
    })

    contracts = await client.search_for_contracts(searchText='NQ', live=False)
    contract_id = contracts[0]['id']

    now = datetime.utcnow()
    all_1min = []
    n_chunks = (DAYS // 7) + 1

    print(f'Fetching {DAYS}j...')
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
        except:
            pass

    data = []
    for b in all_1min:
        d = b if isinstance(b, dict) else b.__dict__
        data.append({
            'datetime': d.get('t') or d.get('datetime'),
            'open': float(d.get('o') or d.get('open') or 0),
            'high': float(d.get('h') or d.get('high') or 0),
            'low': float(d.get('l') or d.get('low') or 0),
            'close': float(d.get('c') or d.get('close') or 0),
            'volume': float(d.get('v') or d.get('volume') or 1),
        })
    df = pd.DataFrame(data)
    df['datetime'] = pd.to_datetime(df['datetime'], utc=True)
    df = df.sort_values('datetime').drop_duplicates('datetime').set_index('datetime')
    print(f'{len(df)} bars')

    # Analyze bar-level volatility
    bar_ranges = (df['high'] - df['low']).values
    bar_moves = abs(df['close'] - df['open']).values

    print()
    print('=' * 55)
    print('VOLATILITE PAR BARRE 1MIN')
    print('=' * 55)
    for name, vals in [('Range (H-L)', bar_ranges), ('Move |C-O|', bar_moves)]:
        arr = np.array(vals)
        print(f'{name}:')
        print(f'  mean={arr.mean():.2f}  std={arr.std():.2f}')
        for p in [25, 50, 75, 90, 95]:
            print(f'  p{p} = {np.percentile(arr, p):.2f}')
        print()

    # Analyze adverse excursion after fake breakout signals
    # Simulate: after a close back inside VA, how much does price move against?
    print('=' * 55)
    print('ADVERSE EXCURSION AFTER ENTRY (simulated)')
    print('=' * 55)

    # Group by day
    df['date'] = df.index.date
    trading_days = sorted(df['date'].unique())

    adverse_excursions = []
    favorable_excursions = []

    for day in trading_days:
        day_df = df[df['date'] == day]
        if len(day_df) < 60:
            continue

        closes = day_df['close'].values
        highs = day_df['high'].values
        lows = day_df['low'].values
        vols = day_df['volume'].values

        # Simple VA calculation
        rounded = np.round(closes * 4) / 4
        unique_p, inv = np.unique(rounded, return_inverse=True)
        vol_at = np.zeros(len(unique_p))
        np.add.at(vol_at, inv, vols)

        if len(unique_p) == 0:
            continue

        vpoc_idx = np.argmax(vol_at)
        total_v = vol_at.sum()
        target_v = total_v * 0.70
        si = np.argsort(vol_at)[::-1]
        cumv = 0
        va_idx = []
        for idx in si:
            cumv += vol_at[idx]
            va_idx.append(idx)
            if cumv >= target_v:
                break
        va_p = unique_p[va_idx]
        vah = float(va_p.max())
        val_ = float(va_p.min())

        # Look for fake breakout signals
        for i in range(62, len(day_df) - 20):
            # Fake breakout UP: high above VAH, close back inside
            if day_df.iloc[i-2]['high'] > vah and day_df.iloc[i-1]['high'] > vah and day_df.iloc[i]['close'] < vah:
                entry = day_df.iloc[i]['close']
                # Track adverse (up) and favorable (down) excursion for 20 bars
                max_adverse = 0
                max_favorable = 0
                for j in range(1, min(21, len(day_df) - i)):
                    h = day_df.iloc[i+j]['high']
                    l = day_df.iloc[i+j]['low']
                    adverse = h - entry  # short, so up is adverse
                    favorable = entry - l
                    max_adverse = max(max_adverse, adverse)
                    max_favorable = max(max_favorable, favorable)
                adverse_excursions.append(max_adverse)
                favorable_excursions.append(max_favorable)

            # Fake breakout DOWN: low below VAL, close back inside
            if day_df.iloc[i-2]['low'] < val_ and day_df.iloc[i-1]['low'] < val_ and day_df.iloc[i]['close'] > val_:
                entry = day_df.iloc[i]['close']
                max_adverse = 0
                max_favorable = 0
                for j in range(1, min(21, len(day_df) - i)):
                    h = day_df.iloc[i+j]['high']
                    l = day_df.iloc[i+j]['low']
                    adverse = entry - l  # long, so down is adverse
                    favorable = h - entry
                    max_adverse = max(max_adverse, adverse)
                    max_favorable = max(max_favorable, favorable)
                adverse_excursions.append(max_adverse)
                favorable_excursions.append(max_favorable)

    print(f'Signaux fake breakout trouves: {len(adverse_excursions)}')
    print()

    if adverse_excursions:
        ae = np.array(adverse_excursions)
        fe = np.array(favorable_excursions)

        print('MAX ADVERSE EXCURSION (MAE) - 20 bars apres entree:')
        for p in [25, 50, 75, 90, 95]:
            print(f'  p{p} = {np.percentile(ae, p):.1f} pts')
        print(f'  mean = {ae.mean():.1f} pts')
        print()

        print('MAX FAVORABLE EXCURSION (MFE) - 20 bars apres entree:')
        for p in [25, 50, 75, 90, 95]:
            print(f'  p{p} = {np.percentile(fe, p):.1f} pts')
        print(f'  mean = {fe.mean():.1f} pts')
        print()

        # Optimal stop = between p75 and p90 of MAE (allow normal noise)
        print('--- RECOMMANDATIONS ---')
        stop_opt = np.percentile(ae, 80)
        print(f'Stop optimal (p80 MAE): {stop_opt:.0f} pts (${stop_opt*20:.0f})')
        print(f'Stop conservateur (p90 MAE): {np.percentile(ae, 90):.0f} pts')
        print()

        # Optimal trail: should not be tighter than median bar range
        med_range = np.median(bar_ranges)
        print(f'Median bar range: {med_range:.1f} pts')
        print(f'Trail minimum recommande: {med_range * 2:.0f} pts (2x median range)')
        print(f'Trail optimal: {med_range * 3:.0f} pts (3x median range)')
        print()

        # Win rate at various stop levels
        print('--- WIN RATE PAR STOP LEVEL ---')
        for stop in [5, 8, 10, 15, 20, 25, 30, 40, 50]:
            # A trade wins if MFE > stop before MAE > stop
            # Simplified: just check if stop would not be hit
            not_stopped = sum(1 for a in ae if a < stop)
            print(f'  Stop={stop:2d}pts: {not_stopped}/{len(ae)} survive ({not_stopped/len(ae)*100:.0f}%)')

    await client.logout()
    print('\nTermine.')


asyncio.run(run())
