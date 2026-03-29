"""Analyse: la direction pre-news predit-elle le mouvement pendant la news ?"""
import sys
from pathlib import Path
import pandas as pd
import numpy as np

BASE_DIR = Path(__file__).resolve().parent.parent
df = pd.read_csv(BASE_DIR / 'data' / 'databento_nq_5min_5y.csv',
                 index_col=0, parse_dates=True)
if df.index.tz is None:
    df.index = df.index.tz_localize('UTC')

cal = pd.read_csv(BASE_DIR / 'data' / 'news_calendar_clean.csv')
cal_t1 = cal[cal['tier'] == 1]  # NFP, CPI, FOMC

print("=== ANALYSE: DIRECTION PRE-NEWS vs MOUVEMENT NEWS ===")
print("Events Tier 1 (NFP, CPI, FOMC): {} slots".format(len(cal_t1)))
print()

results = []

for _, event_row in cal_t1.iterrows():
    date_str = event_row['date']
    time_et = event_row['time_et']
    event_name = event_row['events']

    try:
        event_dt_et = pd.Timestamp(date_str + ' ' + time_et, tz='America/New_York')
        event_dt_utc = event_dt_et.tz_convert('UTC')
    except Exception:
        continue

    # Pre-news: differents lookbacks
    for lb_min in [15, 30, 60, 120]:
        lb_start = event_dt_utc - pd.Timedelta(minutes=lb_min)
        lb_end = event_dt_utc - pd.Timedelta(minutes=2)

        mask_pre = (df.index >= lb_start) & (df.index <= lb_end)
        pre_bars = df[mask_pre]
        if len(pre_bars) < 2:
            continue

        pre_move = pre_bars['close'].iloc[-1] - pre_bars['close'].iloc[0]

        # Post-news: differentes fenetres
        for post_min in [5, 15, 30, 60]:
            post_start = event_dt_utc
            post_end = event_dt_utc + pd.Timedelta(minutes=post_min)

            mask_post = (df.index >= post_start) & (df.index <= post_end)
            post_bars = df[mask_post]
            if len(post_bars) < 1:
                continue

            post_move = post_bars['close'].iloc[-1] - post_bars['close'].iloc[0]

            # Direction match?
            if abs(pre_move) < 5:  # mouvement insignifiant
                pre_dir = 'flat'
            else:
                pre_dir = 'up' if pre_move > 0 else 'down'

            same_dir = (pre_move > 0 and post_move > 0) or (pre_move < 0 and post_move < 0)

            results.append({
                'date': date_str, 'event': event_name, 'time_et': time_et,
                'lb_min': lb_min, 'post_min': post_min,
                'pre_move': round(pre_move, 1), 'post_move': round(post_move, 1),
                'pre_dir': pre_dir, 'same_dir': same_dir,
                'abs_pre': abs(pre_move), 'abs_post': abs(post_move),
            })

rdf = pd.DataFrame(results)

# === TABLEAU: pour chaque combo lookback/post, quel % du temps la direction continue ===
print("=" * 100)
print("  TAUX DE CONTINUATION (pre-news direction = post-news direction)")
print("  Excluant les mouvements pre-news < 5 pts (flat)")
print("=" * 100)
print()
print("  {:>12} {:>8} {:>8} {:>8} {:>8}".format(
    'Lookback', 'Post 5m', 'Post 15m', 'Post 30m', 'Post 60m'))
print("  " + "-" * 50)

for lb in [15, 30, 60, 120]:
    row = "  {:>10}m".format(lb)
    for pm in [5, 15, 30, 60]:
        sub = rdf[(rdf['lb_min'] == lb) & (rdf['post_min'] == pm) & (rdf['pre_dir'] != 'flat')]
        if len(sub) > 0:
            pct = sub['same_dir'].sum() / len(sub) * 100
            row += " {:>6.1f}% ".format(pct)
        else:
            row += "     N/A "
    print(row)

# === Meme chose mais filtrer par force du mouvement pre-news ===
print()
print("=" * 100)
print("  CONTINUATION PAR FORCE DU MOUVEMENT PRE-NEWS (lookback=30min, post=30min)")
print("=" * 100)
print()

sub30 = rdf[(rdf['lb_min'] == 30) & (rdf['post_min'] == 30) & (rdf['pre_dir'] != 'flat')]

thresholds = [5, 10, 15, 20, 30, 40, 50, 75, 100]
print("  {:>15} {:>8} {:>10} {:>12} {:>12} {:>12}".format(
    'Pre-move >=', 'Events', 'Continue%', 'AvgPostMove', 'AvgPost(same)', 'AvgPost(rev)'))
print("  " + "-" * 75)

for th in thresholds:
    s = sub30[sub30['abs_pre'] >= th]
    if len(s) < 5:
        continue
    cont_pct = s['same_dir'].sum() / len(s) * 100
    avg_post = s['abs_post'].mean()

    same = s[s['same_dir'] == True]
    rev = s[s['same_dir'] == False]
    avg_same = same['abs_post'].mean() if len(same) > 0 else 0
    avg_rev = rev['abs_post'].mean() if len(rev) > 0 else 0

    print("  {:>12} pts {:>8} {:>9.1f}% {:>10.1f} pts {:>10.1f} pts {:>10.1f} pts".format(
        th, len(s), cont_pct, avg_post, avg_same, avg_rev))

# === Par type d'event ===
print()
print("=" * 100)
print("  CONTINUATION PAR TYPE D'EVENT (lookback=30min, post=30min, pre_move>=10pts)")
print("=" * 100)
print()

sub_ev = rdf[(rdf['lb_min'] == 30) & (rdf['post_min'] == 30) & (rdf['abs_pre'] >= 10)]

# Simplify event names
def simplify_event(e):
    if 'Non-Farm' in e or 'NFP' in e:
        return 'NFP'
    elif 'CPI' in e:
        return 'CPI'
    elif 'FOMC' in e or 'Federal Funds' in e:
        return 'FOMC'
    else:
        return 'Other'

sub_ev['event_type'] = sub_ev['event'].apply(simplify_event)

print("  {:>10} {:>8} {:>10} {:>12} {:>10} {:>10}".format(
    'Event', 'Count', 'Continue%', 'AvgPostMove', 'AvgPre', 'PnL(cont)'))
print("  " + "-" * 65)

for ev_type in ['NFP', 'CPI', 'FOMC', 'Other']:
    s = sub_ev[sub_ev['event_type'] == ev_type]
    if len(s) < 3:
        continue
    cont_pct = s['same_dir'].sum() / len(s) * 100
    avg_post = s['abs_post'].mean()
    avg_pre = s['abs_pre'].mean()
    # Simulated PnL if we follow pre-news direction
    pnl_sim = sum(row['post_move'] if row['pre_dir'] == 'up' else -row['post_move']
                  for _, row in s.iterrows())
    print("  {:>10} {:>8} {:>9.1f}% {:>10.1f} pts {:>8.1f} pts {:>+8.1f} pts".format(
        ev_type, len(s), cont_pct, avg_post, avg_pre, pnl_sim))

# === Direction UP vs DOWN separately ===
print()
print("=" * 100)
print("  UP vs DOWN: est-ce que la continuation est symetrique ? (lb=30, post=30, move>=10)")
print("=" * 100)
print()

for direction in ['up', 'down']:
    s = sub30[(sub30['pre_dir'] == direction) & (sub30['abs_pre'] >= 10)]
    if len(s) < 5:
        continue
    cont_pct = s['same_dir'].sum() / len(s) * 100
    avg_post = s['post_move'].mean()
    print("  Pre-news {:>5}: {} events | Continue: {:.1f}% | Avg post move: {:+.1f} pts".format(
        direction, len(s), cont_pct, avg_post))

print()
print("=" * 100)
