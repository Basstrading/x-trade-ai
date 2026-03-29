"""Diagnostic: analyse distribution range_ratio, adx_proxy, vpoc_position sur 120j NQ."""
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
    from collections import Counter

    DAYS = 120

    TOPSTEPX_URLS = ConnectionURLS(
        api_endpoint='https://api.topstepx.com',
        user_hub='https://rtc.topstepx.com/hubs/user',
        market_hub='https://rtc.topstepx.com/hubs/market',
    )

    print('=' * 60)
    print(f'DIAGNOSTIC REGIME -- {DAYS} JOURS NQ')
    print('=' * 60)

    print('Connexion ProjectX...')
    client = ProjectXClient(TOPSTEPX_URLS)
    await client.login({
        'auth_type': 'api_key',
        'userName': os.getenv('PROJECTX_USERNAME'),
        'apiKey': os.getenv('PROJECTX_API_KEY'),
    })

    contracts = await client.search_for_contracts(searchText='NQ', live=False)
    contract_id = contracts[0]['id']
    print(f'Connecte -- NQ id={contract_id}')

    # Fetch 1min data in 7-day chunks
    now = datetime.utcnow()
    all_1min = []
    n_chunks = (DAYS // 7) + 1

    print(f'Recuperation {DAYS}j de donnees 1min...')
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
                if chunk_i % 5 == 0:
                    print(f'  chunk {chunk_i}/{n_chunks} ok ({len(bars)} bars)')
        except Exception as e:
            print(f'  chunk {chunk_i} err: {e}')

    # Convert to DataFrame
    data = []
    for b in all_1min:
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
    df = df.sort_values('datetime').drop_duplicates('datetime').set_index('datetime')
    print(f'Total barres 1min: {len(df)}')

    # Group by trading day
    df['date'] = df.index.date
    trading_days = sorted(df['date'].unique())
    print(f'Jours de trading: {len(trading_days)}')
    print()

    # Analyse each day
    results = []
    shape_counter = Counter()

    for day in trading_days:
        day_df = df[df['date'] == day]
        if len(day_df) < 30:
            continue

        day_high = day_df['high'].max()
        day_low = day_df['low'].min()
        day_range = day_high - day_low
        if day_range < 1:
            continue

        day_open = day_df['open'].iloc[0]
        day_close = day_df['close'].iloc[-1]

        # Build volume profile (same as engine.py)
        n_levels = 30
        price_levels = np.linspace(day_low, day_high, n_levels + 1)
        volume_profile = np.zeros(n_levels)

        for _, bar in day_df.iterrows():
            for j in range(n_levels):
                if bar['low'] <= price_levels[j + 1] and bar['high'] >= price_levels[j]:
                    volume_profile[j] += bar['volume']

        total_vol = volume_profile.sum()
        if total_vol == 0:
            continue

        # VPOC
        vpoc_idx = np.argmax(volume_profile)
        vpoc = (price_levels[vpoc_idx] + price_levels[vpoc_idx + 1]) / 2
        vpoc_position = (vpoc - day_low) / day_range

        # Value Area (70%)
        va_target = total_vol * 0.70
        va_vol = volume_profile[vpoc_idx]
        va_low_idx = vpoc_idx
        va_high_idx = vpoc_idx
        while va_vol < va_target:
            expand_down = volume_profile[va_low_idx - 1] if va_low_idx > 0 else 0
            expand_up = volume_profile[va_high_idx + 1] if va_high_idx < n_levels - 1 else 0
            if expand_down >= expand_up and va_low_idx > 0:
                va_low_idx -= 1
                va_vol += volume_profile[va_low_idx]
            elif va_high_idx < n_levels - 1:
                va_high_idx += 1
                va_vol += volume_profile[va_high_idx]
            else:
                break

        vah = price_levels[va_high_idx + 1]
        val = price_levels[va_low_idx]
        va_width = vah - val

        # === INDICATOR 1: range_ratio = va_width / day_range ===
        range_ratio = va_width / day_range

        # === INDICATOR 2: adx_proxy = abs(close - open) / day_range ===
        adx_proxy = abs(day_close - day_open) / day_range

        # === INDICATOR 3: max_tpo (current classifier) ===
        max_tpo = int(volume_profile.max())

        # === INDICATOR 4: volume concentration (top 3 levels / total) ===
        sorted_vols = np.sort(volume_profile)[::-1]
        vol_concentration = sorted_vols[:3].sum() / total_vol if total_vol > 0 else 0

        # === INDICATOR 5: ATR-based directional move ===
        # Simple: how many bars closed in same direction as day move
        day_direction = 1 if day_close > day_open else -1
        aligned_bars = sum(1 for _, bar in day_df.iterrows()
                         if (bar['close'] - bar['open']) * day_direction > 0)
        alignment_ratio = aligned_bars / len(day_df)

        # Current engine classification
        if max_tpo <= 5:
            current_shape = 'thin'
        elif vpoc_position > 0.65:
            current_shape = 'p_shape'
        elif vpoc_position < 0.35:
            current_shape = 'b_shape'
        else:
            current_shape = 'd_shape'

        shape_counter[current_shape] += 1

        # Expected regime based on indicators
        # trending: narrow VA (low range_ratio), high directional move (high adx_proxy)
        # ranging: wide VA (high range_ratio), low directional move (low adx_proxy)
        results.append({
            'date': str(day),
            'range_ratio': round(range_ratio, 3),
            'adx_proxy': round(adx_proxy, 3),
            'vpoc_position': round(vpoc_position, 3),
            'max_tpo': max_tpo,
            'vol_concentration': round(vol_concentration, 3),
            'alignment_ratio': round(alignment_ratio, 3),
            'day_range': round(day_range, 1),
            'va_width': round(va_width, 1),
            'current_shape': current_shape,
            'day_pnl_pts': round(day_close - day_open, 1),
        })

    print('=' * 60)
    print(f'RESULTATS SUR {len(results)} JOURS')
    print('=' * 60)
    print()

    # Current classification distribution
    print('--- CLASSIFICATION ACTUELLE (engine.py) ---')
    for shape, count in shape_counter.most_common():
        print(f'  {shape:10} : {count:3} jours ({count/len(results)*100:.0f}%)')
    print()

    # Extract arrays
    rr = [r['range_ratio'] for r in results]
    adx = [r['adx_proxy'] for r in results]
    vp = [r['vpoc_position'] for r in results]
    mt = [r['max_tpo'] for r in results]
    vc = [r['vol_concentration'] for r in results]
    ar = [r['alignment_ratio'] for r in results]

    def show_dist(name, vals):
        arr = np.array(vals)
        print(f'--- {name} ---')
        print(f'  min={arr.min():.3f}  max={arr.max():.3f}  mean={arr.mean():.3f}  std={arr.std():.3f}')
        for p in [10, 25, 33, 50, 67, 75, 90]:
            print(f'  p{p:2d} = {np.percentile(arr, p):.3f}')
        print()

    show_dist('RANGE_RATIO (va_width / day_range)', rr)
    show_dist('ADX_PROXY (|close-open| / day_range)', adx)
    show_dist('VPOC_POSITION ((vpoc-low) / range)', vp)
    show_dist('MAX_TPO (max volume at single level)', mt)
    show_dist('VOL_CONCENTRATION (top3 levels / total)', vc)
    show_dist('ALIGNMENT_RATIO (bars aligned with day dir)', ar)

    # Proposed classification with seuils
    print('=' * 60)
    print('CLASSIFICATION PROPOSEE')
    print('=' * 60)

    # Use percentiles as seuils
    rr_p33 = np.percentile(rr, 33)
    rr_p67 = np.percentile(rr, 67)
    adx_p33 = np.percentile(adx, 33)
    adx_p67 = np.percentile(adx, 67)

    print(f'Seuils range_ratio: trending < {rr_p33:.3f} | ranging > {rr_p67:.3f}')
    print(f'Seuils adx_proxy:   ranging < {adx_p33:.3f} | trending > {adx_p67:.3f}')
    print()

    new_counter = Counter()
    trending_days = []
    ranging_days = []
    mixed_days = []

    for r in results:
        score_trending = 0
        score_ranging = 0

        # range_ratio: low = trending, high = ranging
        if r['range_ratio'] < rr_p33:
            score_trending += 1
        elif r['range_ratio'] > rr_p67:
            score_ranging += 1

        # adx_proxy: high = trending, low = ranging
        if r['adx_proxy'] > adx_p67:
            score_trending += 1
        elif r['adx_proxy'] < adx_p33:
            score_ranging += 1

        # vpoc_position: extreme = trending (p/b shape), middle = ranging
        if r['vpoc_position'] > 0.65 or r['vpoc_position'] < 0.35:
            score_trending += 1
        elif 0.40 < r['vpoc_position'] < 0.60:
            score_ranging += 1

        if score_trending >= 2:
            regime = 'trending'
            trending_days.append(r)
        elif score_ranging >= 2:
            regime = 'ranging'
            ranging_days.append(r)
        else:
            regime = 'mixed'
            mixed_days.append(r)

        r['proposed_regime'] = regime
        new_counter[regime] += 1

    print('--- NOUVELLE DISTRIBUTION ---')
    for regime, count in new_counter.most_common():
        print(f'  {regime:10} : {count:3} jours ({count/len(results)*100:.0f}%)')
    print()

    # Verify: are trending days actually directional?
    print('--- VALIDATION ---')
    if trending_days:
        avg_adx_t = np.mean([d['adx_proxy'] for d in trending_days])
        avg_rr_t = np.mean([d['range_ratio'] for d in trending_days])
        avg_range_t = np.mean([d['day_range'] for d in trending_days])
        print(f'  Trending ({len(trending_days)}j): avg_adx={avg_adx_t:.3f} avg_rr={avg_rr_t:.3f} avg_range={avg_range_t:.0f}pts')
    if ranging_days:
        avg_adx_r = np.mean([d['adx_proxy'] for d in ranging_days])
        avg_rr_r = np.mean([d['range_ratio'] for d in ranging_days])
        avg_range_r = np.mean([d['day_range'] for d in ranging_days])
        print(f'  Ranging  ({len(ranging_days)}j): avg_adx={avg_adx_r:.3f} avg_rr={avg_rr_r:.3f} avg_range={avg_range_r:.0f}pts')
    if mixed_days:
        avg_adx_m = np.mean([d['adx_proxy'] for d in mixed_days])
        avg_rr_m = np.mean([d['range_ratio'] for d in mixed_days])
        avg_range_m = np.mean([d['day_range'] for d in mixed_days])
        print(f'  Mixed    ({len(mixed_days)}j): avg_adx={avg_adx_m:.3f} avg_rr={avg_rr_m:.3f} avg_range={avg_range_m:.0f}pts')
    print()

    # Strategy mapping
    print('--- MAPPING STRATEGY ---')
    print('  trending -> breakout (real breakout trades)')
    print('  ranging  -> fake_breakout (fade failed probes)')
    print('  mixed    -> fake_breakout (conservative)')
    print()

    # Show sample days
    print('=' * 60)
    print('EXEMPLES JOURS TRENDING (breakout)')
    print('=' * 60)
    for d in sorted(trending_days, key=lambda x: x['adx_proxy'], reverse=True)[:10]:
        print(f"  {d['date']} rr={d['range_ratio']:.3f} adx={d['adx_proxy']:.3f} vpoc={d['vpoc_position']:.3f} "
              f"range={d['day_range']:.0f}pts pnl={d['day_pnl_pts']:+.0f}pts was={d['current_shape']}")
    print()

    print('=' * 60)
    print('EXEMPLES JOURS RANGING (fake_breakout)')
    print('=' * 60)
    for d in sorted(ranging_days, key=lambda x: x['range_ratio'], reverse=True)[:10]:
        print(f"  {d['date']} rr={d['range_ratio']:.3f} adx={d['adx_proxy']:.3f} vpoc={d['vpoc_position']:.3f} "
              f"range={d['day_range']:.0f}pts pnl={d['day_pnl_pts']:+.0f}pts was={d['current_shape']}")
    print()

    # Final seuils for engine.py
    print('=' * 60)
    print('SEUILS FINAUX POUR ENGINE.PY')
    print('=' * 60)
    print(f"REGIME_THRESHOLDS = {{")
    print(f"    'range_ratio_low': {rr_p33:.3f},   # below = trending signal")
    print(f"    'range_ratio_high': {rr_p67:.3f},  # above = ranging signal")
    print(f"    'adx_proxy_low': {adx_p33:.3f},    # below = ranging signal")
    print(f"    'adx_proxy_high': {adx_p67:.3f},   # above = trending signal")
    print(f"    'vpoc_extreme': 0.35,       # vpoc < 0.35 or > 0.65 = trending signal")
    print(f"}}")
    print()
    print('classify_regime():')
    print('  score_trending >= 2 -> "trending" -> breakout strategy')
    print('  score_ranging >= 2  -> "ranging"  -> fake_breakout strategy')
    print('  else                -> "mixed"    -> fake_breakout (conservative)')

    await client.logout()
    print('\nTermine.')


asyncio.run(run())
