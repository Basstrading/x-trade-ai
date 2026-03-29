"""
MM20 PULLBACK + NEWS LIVE TRADING — Topstep $50k — MNQ x4
===========================================================
Strategie 1: MM20 Pullback (16h-20h39 Paris)
  - SMA20 M5/H1 + Pullback(10,15) + h1_sma_dist>=75
  - Trail 20 bars + max_sl=200 + TP 300 + dls=3
Strategie 2: News NFP/CPI (8h28 ET, ~2-3x/mois)
  - Direction pre-news (30min lookback, move>=20pts)
  - SL 100pts + TP 150pts + hold max 60min
"""

import asyncio
import json
import os
import sys
from datetime import datetime, date, timedelta
from pathlib import Path
from collections import deque

sys.path.insert(0, '.')
from dotenv import load_dotenv
load_dotenv()

from loguru import logger
from projectx_api import (
    ProjectXClient, ConnectionURLS,
    OrderSide, OrderType, AggregationUnit
)

from backtester.opr_engine import is_dst_gap, PARIS_TZ
import pandas as pd
import pytz

# ── CONFIG ──────────────────────────────────────────────
TOPSTEPX_URLS = ConnectionURLS(
    api_endpoint='https://api.topstepx.com',
    user_hub='https://rtc.topstepx.com/hubs/user',
    market_hub='https://rtc.topstepx.com/hubs/market',
)

CONFIG_PATH = Path('data/config_production.json')

INSTRUMENT = 'MNQ'
CONTRACTS = 4              # 4 MNQ
POINT_VALUE = 2.0          # MNQ = $2/pt
TOTAL_PV = CONTRACTS * POINT_VALUE  # $8/pt (= backtest)

# MM20 Params (backtest-validated 5 ans)
MM20_TP_PTS = 300
MM20_TRAIL_BARS = 20            # trailing: low/high des 20 dernieres bougies
MM20_MAX_SL_PTS = 200
MM20_MAX_TRADES_DAY = 4
MM20_SMA_PERIOD = 20
MM20_PULLBACK_BARS = 10
MM20_PULLBACK_DIST = 15
MM20_H1_SMA_DIST = 75
MM20_DAILY_LOSS_STOP = 3   # pertes consecutives
MM20_DAILY_LOSS_USD = 1000
MM20_COOLDOWN_SEC = 300     # 5 min minimum entre 2 trades MM20

# News Params (backtest-validated 5 ans)
NEWS_LOOKBACK_MIN = 30
NEWS_MIN_MOVE_PTS = 20
NEWS_ENTRY_BEFORE_MIN = 2
NEWS_SL_PTS = 100
NEWS_TP_PTS = 150
NEWS_MAX_HOLD_MIN = 60

# Risk
DAILY_LOSS_LIMIT = -1000

# Logs
os.makedirs('logs', exist_ok=True)
logger.add("logs/mm20_news_{time:YYYY-MM-DD}.log", rotation="1 day", retention="30 days")

STATE_PATH = Path('data/mm20_news_state.json')
TRADES_HISTORY_PATH = Path('data/trades_history.json')
NEWS_CALENDAR_PATH = Path('data/news_calendar_nfp_cpi.csv')


class MM20NewsLive:
    """Moteur live MM20 Pullback + News NFP/CPI."""

    def __init__(self):
        self.client = None
        self.contract_id = None

        # Charge config
        self.account_id = None
        self.copy_accounts = []
        try:
            cfg = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
            if cfg.get('account_id'):
                self.account_id = int(cfg['account_id'])
            self.copy_accounts = [int(x) for x in cfg.get('copy_accounts', [])]
        except Exception:
            pass
        if not self.account_id:
            self.account_id = int(os.getenv('ACCOUNT_ID', '0'))

        # Etat journalier
        self.today = None
        self.daily_pnl = 0.0
        self.trades_today = 0
        self.consec_losses = 0
        self._trades_log = []

        # Etat MM20
        self.mm20_in_trade = False
        self.mm20_direction = ''
        self.mm20_entry_price = 0.0
        self.mm20_tp_price = 0.0
        self.mm20_sl_price = 0.0
        self.mm20_trail_stop = 0.0
        self.mm20_entry_time = None
        self.mm20_last_exit_time = None  # cooldown anti-machine-gunning

        # Etat News
        self.news_in_trade = False
        self.news_direction = ''
        self.news_entry_price = 0.0
        self.news_tp_price = 0.0
        self.news_sl_price = 0.0
        self.news_entry_time = None
        self.news_max_exit_time = None
        self.news_calendar = None
        self._load_news_calendar()

        # Barres 5min cache
        self.bars_5min = []
        self.bars_1h = []

        # Horaires du jour
        self.start_h = 16
        self.start_m = 0
        self.close_h = 20
        self.close_m = 39

        # Restauration
        self._restore_daily_state()

    def _load_news_calendar(self):
        """Charge le calendrier NFP/CPI."""
        try:
            if NEWS_CALENDAR_PATH.exists():
                self.news_calendar = pd.read_csv(NEWS_CALENDAR_PATH)
                logger.info("Calendrier news charge: {} events".format(len(self.news_calendar)))
            else:
                logger.warning("Calendrier news non trouve: {}".format(NEWS_CALENDAR_PATH))
                self.news_calendar = pd.DataFrame()
        except Exception as e:
            logger.error("Erreur chargement calendrier: {}".format(e))
            self.news_calendar = pd.DataFrame()

    def _restore_daily_state(self):
        """Restaure l'etat du jour depuis le fichier state."""
        try:
            if not STATE_PATH.exists():
                return
            saved = json.loads(STATE_PATH.read_text(encoding='utf-8'))
            saved_ts = saved.get('timestamp', '')
            if not saved_ts:
                return
            saved_date = saved_ts[:10]
            today_str = datetime.now(PARIS_TZ).strftime('%Y-%m-%d')
            if saved_date != today_str:
                return

            self.daily_pnl = saved.get('daily_pnl', 0.0)
            self.trades_today = saved.get('trades_today', 0)
            self.consec_losses = saved.get('consec_losses', 0)
            self._trades_log = saved.get('trades', [])
            self.today = datetime.now(PARIS_TZ).date()

            # Calcul horaires DST (sinon new_day() skip car self.today deja set)
            dst_gap = is_dst_gap(self.today)
            if dst_gap:
                self.start_h, self.start_m = 15, 0
                self.close_h, self.close_m = 19, 39
            else:
                self.start_h, self.start_m = 16, 0
                self.close_h, self.close_m = 20, 39

            if self._trades_log or self.daily_pnl != 0:
                logger.info("Etat restaure: {} trades, PnL=${:+,.0f}".format(
                    self.trades_today, self.daily_pnl))
        except Exception as e:
            logger.debug("Restauration etat: {}".format(e))

    async def connect(self):
        """Connexion API TopstepX."""
        self.client = ProjectXClient(TOPSTEPX_URLS)
        await self.client.login({
            'auth_type': 'api_key',
            'userName': os.getenv('PROJECTX_USERNAME'),
            'apiKey': os.getenv('PROJECTX_API_KEY'),
        })

        contracts = await self.client.search_for_contracts(searchText=INSTRUMENT, live=False)
        c = contracts[0]
        self.contract_id = c['id'] if isinstance(c, dict) else c.id
        logger.success("Connecte — {} id={} compte={}".format(
            INSTRUMENT, self.contract_id, self.account_id))

        # Detecte positions existantes
        try:
            positions = await self.client.search_for_positions(accountId=self.account_id)
            for pos in positions:
                p = pos if isinstance(pos, dict) else pos.__dict__
                cid = p.get('contractId', '')
                if str(self.contract_id) in str(cid):
                    size = p.get('size', 0)
                    ptype = p.get('type', 0)
                    direction = 'long' if ptype == 1 else 'short'
                    avg_price = p.get('averagePrice', 0)
                    # Assume it's an MM20 trade
                    self.mm20_in_trade = True
                    self.mm20_direction = direction
                    self.mm20_entry_price = avg_price
                    if direction == 'long':
                        self.mm20_tp_price = avg_price + MM20_TP_PTS
                        self.mm20_sl_price = avg_price - MM20_MAX_SL_PTS
                        self.mm20_trail_stop = avg_price - MM20_MAX_SL_PTS
                    else:
                        self.mm20_tp_price = avg_price - MM20_TP_PTS
                        self.mm20_sl_price = avg_price + MM20_MAX_SL_PTS
                        self.mm20_trail_stop = avg_price + MM20_MAX_SL_PTS
                    logger.warning("POSITION EXISTANTE: {} x{} @ {:.2f}".format(
                        direction.upper(), size, avg_price))
                    break
        except Exception as e:
            logger.debug("Verification positions: {}".format(e))

    def new_day(self):
        """Reset journalier + calcul horaires DST."""
        now_paris = datetime.now(PARIS_TZ)
        today = now_paris.date()
        if self.today == today:
            return
        if self.today is not None:
            self._save_daily_summary()

        self.today = today
        self._trades_log = []
        self.daily_pnl = 0.0
        self.trades_today = 0
        self.consec_losses = 0
        self.mm20_in_trade = False
        self.news_in_trade = False
        self.bars_5min = []
        self.bars_1h = []

        # DST
        dst_gap = is_dst_gap(today)
        if dst_gap:
            self.start_h, self.start_m = 15, 0
            self.close_h, self.close_m = 19, 39
        else:
            self.start_h, self.start_m = 16, 0
            self.close_h, self.close_m = 20, 39

        logger.info("=== NOUVEAU JOUR {} === {} | MM20 {}h{:02d}-{}h{:02d}".format(
            today, "DST" if dst_gap else "Normal",
            self.start_h, self.start_m, self.close_h, self.close_m))

    # ── BARS FETCHING ──────────────────────────────────────

    async def fetch_bars_5min(self, lookback_hours=6):
        """Recupere les barres 5min recentes."""
        now_utc = datetime.utcnow()
        start_utc = now_utc - timedelta(hours=lookback_hours)
        try:
            bars = await self.client.retrieve_bars(
                contractId=self.contract_id, live=False,
                startTime=start_utc, endTime=now_utc,
                unit=AggregationUnit.MINUTE, unitNumber=5,
                limit=200, includePartialBar=False,
            )
            if bars:
                self.bars_5min = []
                for b in bars:
                    d = b if isinstance(b, dict) else b.__dict__
                    self.bars_5min.append({
                        'datetime': d.get('t') or d.get('datetime') or d.get('timestamp'),
                        'open': float(d.get('o') or d.get('open') or 0),
                        'high': float(d.get('h') or d.get('high') or 0),
                        'low': float(d.get('l') or d.get('low') or 0),
                        'close': float(d.get('c') or d.get('close') or 0),
                        'volume': float(d.get('v') or d.get('volume') or 0),
                    })
                return True
        except Exception as e:
            logger.error("Erreur fetch barres 5min: {}".format(e))
        return False

    async def fetch_bars_1h(self, lookback_hours=48):
        """Recupere les barres 1h recentes."""
        now_utc = datetime.utcnow()
        start_utc = now_utc - timedelta(hours=lookback_hours)
        try:
            bars = await self.client.retrieve_bars(
                contractId=self.contract_id, live=False,
                startTime=start_utc, endTime=now_utc,
                unit=AggregationUnit.HOUR, unitNumber=1,
                limit=50, includePartialBar=False,
            )
            if bars:
                self.bars_1h = []
                for b in bars:
                    d = b if isinstance(b, dict) else b.__dict__
                    self.bars_1h.append({
                        'datetime': d.get('t') or d.get('datetime') or d.get('timestamp'),
                        'open': float(d.get('o') or d.get('open') or 0),
                        'high': float(d.get('h') or d.get('high') or 0),
                        'low': float(d.get('l') or d.get('low') or 0),
                        'close': float(d.get('c') or d.get('close') or 0),
                        'volume': float(d.get('v') or d.get('volume') or 0),
                    })
                return True
        except Exception as e:
            logger.error("Erreur fetch barres 1h: {}".format(e))
        return False

    def _compute_sma(self, bars, period):
        """Calcule SMA sur les N dernieres barres."""
        if len(bars) < period:
            return None
        closes = [b['close'] for b in bars[-period:]]
        return sum(closes) / period

    # ── PRICE ──────────────────────────────────────────────

    async def get_price(self):
        """Prix actuel via derniere barre 5sec."""
        now_utc = datetime.utcnow()
        try:
            bars = await self.client.retrieve_bars(
                contractId=self.contract_id, live=False,
                startTime=now_utc - timedelta(seconds=30),
                endTime=now_utc,
                unit=AggregationUnit.SECOND, unitNumber=5,
                limit=5, includePartialBar=True,
            )
            if bars:
                b = bars[-1]
                d = b if isinstance(b, dict) else b.__dict__
                return float(d.get('c') or d.get('close') or 0)
        except Exception as e:
            logger.debug("Erreur prix: {}".format(e))
        return None

    # ── ORDERS ─────────────────────────────────────────────

    def reload_copy_accounts(self):
        try:
            cfg = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
            self.copy_accounts = [int(x) for x in cfg.get('copy_accounts', [])]
        except Exception:
            pass

    async def place_market_order(self, side, size):
        """Place un ordre market sur master + copies."""
        order_side = OrderSide.BUY if side == 'long' else OrderSide.SELL
        order = await self.client.place_order(
            accountId=self.account_id,
            contractId=str(self.contract_id),
            type=OrderType.MARKET,
            side=order_side,
            size=size,
        )
        logger.info("ORDRE {} x{} @ MARKET (master {})".format(
            side.upper(), size, self.account_id))

        self.reload_copy_accounts()
        for copy_id in self.copy_accounts:
            try:
                await self.client.place_order(
                    accountId=copy_id,
                    contractId=str(self.contract_id),
                    type=OrderType.MARKET,
                    side=order_side,
                    size=size,
                )
                logger.success("COPY {} x{} -> compte {}".format(side.upper(), size, copy_id))
            except Exception as e:
                logger.error("COPY ERREUR compte {}: {}".format(copy_id, e))
        return order

    async def close_position(self):
        """Ferme la position sur master + copies."""
        try:
            await self.client.close_position(
                accountId=self.account_id,
                contractId=str(self.contract_id),
            )
            logger.info("Position fermee (master {})".format(self.account_id))
        except Exception as e:
            logger.error("Erreur fermeture: {}".format(e))
            # Fallback: passe un ordre inverse
            if self.mm20_in_trade:
                side = 'short' if self.mm20_direction == 'long' else 'long'
            elif self.news_in_trade:
                side = 'short' if self.news_direction == 'long' else 'long'
            else:
                return
            order_side = OrderSide.BUY if side == 'long' else OrderSide.SELL
            await self.client.place_order(
                accountId=self.account_id,
                contractId=str(self.contract_id),
                type=OrderType.MARKET,
                side=order_side,
                size=CONTRACTS,
            )

        self.reload_copy_accounts()
        for copy_id in self.copy_accounts:
            try:
                await self.client.close_position(
                    accountId=copy_id,
                    contractId=str(self.contract_id),
                )
                logger.success("COPY fermeture -> compte {}".format(copy_id))
            except Exception as e:
                logger.error("COPY fermeture ERREUR {}: {}".format(copy_id, e))

    # ── MM20 STRATEGY ──────────────────────────────────────

    def _check_mm20_entry(self, price):
        """Verifie les conditions d'entree MM20. Retourne direction ou None."""
        if len(self.bars_5min) < MM20_SMA_PERIOD + MM20_PULLBACK_BARS:
            return None
        if len(self.bars_1h) < MM20_SMA_PERIOD:
            return None

        # SMA20 M5
        sma20_5 = self._compute_sma(self.bars_5min, MM20_SMA_PERIOD)
        # SMA20 H1
        sma20_1h = self._compute_sma(self.bars_1h, MM20_SMA_PERIOD)
        # Close H1
        close_1h = self.bars_1h[-1]['close']
        # Close M5
        close_5 = self.bars_5min[-1]['close']

        if sma20_5 is None or sma20_1h is None:
            return None

        # Filtre distance H1 SMA >= 75 pts
        h1_dist = abs(close_1h - sma20_1h)
        if h1_dist < MM20_H1_SMA_DIST:
            return None

        # Direction: close M5 vs SMA20 M5 + close H1 vs SMA20 H1
        direction = None
        if close_5 > sma20_5 and close_1h > sma20_1h:
            direction = 'long'
        elif close_5 < sma20_5 and close_1h < sma20_1h:
            direction = 'short'

        if direction is None:
            return None

        # Pullback: dans les 10 dernieres bougies M5, le low/high a touche SMA20
        pb_bars = self.bars_5min[-MM20_PULLBACK_BARS:]
        pb_closes = [b['close'] for b in self.bars_5min[-(MM20_SMA_PERIOD + MM20_PULLBACK_BARS):]]
        pullback_ok = False

        for idx in range(len(pb_bars)):
            # SMA20 au moment de cette barre (approximation avec les barres disponibles)
            bar_pos = len(self.bars_5min) - MM20_PULLBACK_BARS + idx
            if bar_pos < MM20_SMA_PERIOD:
                continue
            sma_at_bar = sum(b['close'] for b in self.bars_5min[bar_pos - MM20_SMA_PERIOD:bar_pos]) / MM20_SMA_PERIOD

            if direction == 'long':
                dist = pb_bars[idx]['low'] - sma_at_bar
                if dist <= MM20_PULLBACK_DIST:
                    pullback_ok = True
                    break
            else:
                dist = sma_at_bar - pb_bars[idx]['high']
                if dist <= MM20_PULLBACK_DIST:
                    pullback_ok = True
                    break

        if not pullback_ok:
            return None

        return direction

    def _compute_trail_stop(self, direction):
        """Calcule le trailing stop MM20 (low/high des 20 dernieres bougies)."""
        if len(self.bars_5min) < MM20_TRAIL_BARS:
            return None
        recent = self.bars_5min[-MM20_TRAIL_BARS:]
        if direction == 'long':
            return min(b['low'] for b in recent)
        else:
            return max(b['high'] for b in recent)

    async def _manage_mm20_trade(self, price):
        """Gere un trade MM20 ouvert."""
        if not self.mm20_in_trade:
            return

        # Mise a jour trailing stop (monte avec le prix pour verrouiller les gains)
        new_trail = self._compute_trail_stop(self.mm20_direction)
        if new_trail is not None:
            if self.mm20_direction == 'long':
                # Trail ne fait que MONTER (low des 20 dernieres bougies)
                if new_trail > self.mm20_trail_stop:
                    self.mm20_trail_stop = new_trail
            else:
                # Trail ne fait que DESCENDRE (high des 20 dernieres bougies)
                if new_trail < self.mm20_trail_stop:
                    self.mm20_trail_stop = new_trail

        exit_reason = None
        exit_price = price

        # Combine last completed bar high/low + current price for accurate detection
        bar_high = self.bars_5min[-1]['high'] if self.bars_5min else price
        bar_low = self.bars_5min[-1]['low'] if self.bars_5min else price
        effective_high = max(bar_high, price)
        effective_low = min(bar_low, price)

        if self.mm20_direction == 'long':
            # TP (check sur effective high)
            if effective_high >= self.mm20_tp_price:
                exit_reason = 'tp'
                exit_price = self.mm20_tp_price
            # Max SL (check sur effective low)
            elif effective_low <= self.mm20_entry_price - MM20_MAX_SL_PTS:
                exit_reason = 'max_sl'
                exit_price = self.mm20_entry_price - MM20_MAX_SL_PTS
            # Trail stop (check sur effective low)
            elif effective_low <= self.mm20_trail_stop:
                exit_reason = 'trail'
                exit_price = self.mm20_trail_stop
        else:
            # TP (check sur effective low)
            if effective_low <= self.mm20_tp_price:
                exit_reason = 'tp'
                exit_price = self.mm20_tp_price
            # Max SL (check sur effective high)
            elif effective_high >= self.mm20_entry_price + MM20_MAX_SL_PTS:
                exit_reason = 'max_sl'
                exit_price = self.mm20_entry_price + MM20_MAX_SL_PTS
            # Trail stop (check sur effective high)
            elif effective_high >= self.mm20_trail_stop:
                exit_reason = 'trail'
                exit_price = self.mm20_trail_stop

        # Time exit
        now_paris = datetime.now(PARIS_TZ)
        if now_paris.hour > self.close_h or (now_paris.hour == self.close_h and now_paris.minute >= self.close_m):
            exit_reason = 'time'
            exit_price = price

        if exit_reason:
            await self.close_position()
            if self.mm20_direction == 'long':
                pnl_pts = exit_price - self.mm20_entry_price
            else:
                pnl_pts = self.mm20_entry_price - exit_price
            pnl_usd = pnl_pts * TOTAL_PV

            self.daily_pnl += pnl_usd
            self.trades_today += 1
            if pnl_pts < 0:
                self.consec_losses += 1
            else:
                self.consec_losses = 0

            log_fn = logger.success if pnl_pts > 0 else logger.warning
            log_fn("MM20 {} {} @ {:.2f} -> {:.2f} | {:.1f}pts ${:+,.0f} ({}) | Jour: ${:+,.0f}".format(
                exit_reason.upper(), self.mm20_direction.upper(),
                self.mm20_entry_price, exit_price,
                pnl_pts, pnl_usd, exit_reason, self.daily_pnl))

            if self._trades_log:
                self._trades_log[-1].update({
                    'exit': exit_price, 'pnl': pnl_usd,
                    'pnl_pts': round(pnl_pts, 2), 'status': exit_reason
                })
                self._save_trade_to_history(self._trades_log[-1])

            self.mm20_in_trade = False
            self.mm20_last_exit_time = datetime.now(PARIS_TZ)

    # ── NEWS STRATEGY ──────────────────────────────────────

    def _is_news_day(self):
        """Verifie si aujourd'hui a un evenement NFP/CPI."""
        if self.news_calendar is None or self.news_calendar.empty:
            return None
        today_str = str(self.today)
        matches = self.news_calendar[self.news_calendar['date'] == today_str]
        if len(matches) > 0:
            return matches.iloc[0]
        return None

    def _check_news_entry_window(self, news_row):
        """Verifie si on est dans la fenetre d'entree news (2 min avant)."""
        time_et = news_row['time_et']
        hour_et, minute_et = map(int, time_et.split(':'))

        try:
            event_dt_et = datetime(
                self.today.year, self.today.month, self.today.day,
                hour_et, minute_et,
                tzinfo=pytz.timezone('America/New_York')
            )
        except Exception:
            return False, None

        entry_dt = event_dt_et - timedelta(minutes=NEWS_ENTRY_BEFORE_MIN)
        now_et = datetime.now(pytz.timezone('America/New_York'))

        # On est dans la fenetre si: entry_time <= now <= entry_time + 1min
        if entry_dt <= now_et <= entry_dt + timedelta(minutes=1):
            return True, event_dt_et
        return False, None

    async def _check_news_direction(self):
        """Determine la direction pre-news (lookback 30min)."""
        # On a besoin de barres 5min des 30 dernieres minutes = 6 barres
        if len(self.bars_5min) < 7:
            return None

        # Prix il y a 30 min vs maintenant
        price_now = self.bars_5min[-1]['close']
        price_30ago = self.bars_5min[-7]['close']  # 6 barres * 5min = 30min
        move = price_now - price_30ago

        if move >= NEWS_MIN_MOVE_PTS:
            return 'long'
        elif move <= -NEWS_MIN_MOVE_PTS:
            return 'short'
        return None

    async def _manage_news_trade(self, price):
        """Gere un trade news ouvert."""
        if not self.news_in_trade:
            return

        exit_reason = None
        exit_price = price

        if self.news_direction == 'long':
            if price >= self.news_tp_price:
                exit_reason = 'tp'
                exit_price = self.news_tp_price
            elif price <= self.news_sl_price:
                exit_reason = 'sl'
                exit_price = self.news_sl_price
        else:
            if price <= self.news_tp_price:
                exit_reason = 'tp'
                exit_price = self.news_tp_price
            elif price >= self.news_sl_price:
                exit_reason = 'sl'
                exit_price = self.news_sl_price

        # Time exit (60 min max)
        now = datetime.now(pytz.timezone('America/New_York'))
        if self.news_max_exit_time and now >= self.news_max_exit_time:
            exit_reason = 'time'
            exit_price = price

        if exit_reason:
            await self.close_position()
            if self.news_direction == 'long':
                pnl_pts = exit_price - self.news_entry_price
            else:
                pnl_pts = self.news_entry_price - exit_price
            pnl_usd = pnl_pts * TOTAL_PV

            self.daily_pnl += pnl_usd
            self.trades_today += 1
            if pnl_pts < 0:
                self.consec_losses += 1
            else:
                self.consec_losses = 0

            log_fn = logger.success if pnl_pts > 0 else logger.warning
            log_fn("NEWS {} {} @ {:.2f} -> {:.2f} | {:.1f}pts ${:+,.0f} ({})".format(
                exit_reason.upper(), self.news_direction.upper(),
                self.news_entry_price, exit_price,
                pnl_pts, pnl_usd, exit_reason))

            if self._trades_log:
                self._trades_log[-1].update({
                    'exit': exit_price, 'pnl': pnl_usd,
                    'pnl_pts': round(pnl_pts, 2), 'status': exit_reason
                })
                self._save_trade_to_history(self._trades_log[-1])

            self.news_in_trade = False

    # ── MAIN LOOP ──────────────────────────────────────────

    async def check_and_trade(self):
        """Boucle principale — appelee toutes les ~30s."""
        # Guard: arret d'urgence
        stop_flag = Path('data/emergency_stop.flag')
        if stop_flag.exists():
            try:
                flag = json.loads(stop_flag.read_text(encoding='utf-8'))
                if flag.get('stopped'):
                    if self.mm20_in_trade or self.news_in_trade:
                        logger.warning("ARRET D'URGENCE — fermeture position")
                        await self.close_position()
                        self.mm20_in_trade = False
                        self.news_in_trade = False
                    return
            except Exception:
                pass

        # Guard: bot desactive
        if Path('data/bot_disabled.flag').exists():
            return

        # Guard: compte
        if not self.account_id or self.account_id == 0:
            try:
                cfg = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
                if cfg.get('account_id'):
                    self.account_id = int(cfg['account_id'])
            except Exception:
                pass
            if not self.account_id or self.account_id == 0:
                return

        self.new_day()
        now_paris = datetime.now(PARIS_TZ)
        h, m = now_paris.hour, now_paris.minute

        # Guard: verif position reelle
        if self.mm20_in_trade or self.news_in_trade:
            try:
                positions = await self.client.search_for_positions(accountId=self.account_id)
                has_pos = any(
                    str(self.contract_id) in str(
                        p.get('contractId', '') if isinstance(p, dict) else getattr(p, 'contractId', ''))
                    for p in positions
                )
                if not has_pos:
                    logger.warning("Position fermee manuellement — sync")
                    price = await self.get_price()
                    if price:
                        if self.mm20_in_trade:
                            d = self.mm20_direction
                            entry = self.mm20_entry_price
                        else:
                            d = self.news_direction
                            entry = self.news_entry_price
                        pnl_pts = (price - entry) if d == 'long' else (entry - price)
                        pnl_usd = pnl_pts * TOTAL_PV
                        self.daily_pnl += pnl_usd
                        self.trades_today += 1
                        logger.info("MANUAL CLOSE {} PnL~{:+.1f}pts ${:+,.0f}".format(
                            d.upper(), pnl_pts, pnl_usd))
                        if self._trades_log:
                            self._trades_log[-1].update({
                                'exit': price, 'pnl': pnl_usd, 'status': 'manual'
                            })
                            self._save_trade_to_history(self._trades_log[-1])
                    self.mm20_in_trade = False
                    self.news_in_trade = False
            except Exception as e:
                logger.debug("Verif position: {}".format(e))

        # Guard: daily loss limit
        if self.daily_pnl <= DAILY_LOSS_LIMIT:
            if self.mm20_in_trade or self.news_in_trade:
                await self.close_position()
                self.mm20_in_trade = False
                self.news_in_trade = False
            return

        price = await self.get_price()
        if not price:
            return

        # Refresh barres
        await self.fetch_bars_5min()

        # ── GESTION TRADES OUVERTS ──
        if self.mm20_in_trade:
            await self._manage_mm20_trade(price)
        if self.news_in_trade:
            await self._manage_news_trade(price)

        # Si deja en trade, pas de nouvelle entree
        if self.mm20_in_trade or self.news_in_trade:
            return

        # ── NEWS ENTRY (prioritaire car rare et time-sensitive) ──
        news_row = self._is_news_day()
        if news_row is not None:
            in_window, event_dt = self._check_news_entry_window(news_row)
            if in_window and not self.news_in_trade:
                direction = await self._check_news_direction()
                if direction:
                    logger.info("NEWS SIGNAL: {} (event: {})".format(
                        direction.upper(), news_row.get('events', '?')))

                    await self.place_market_order(direction, CONTRACTS)

                    self.news_in_trade = True
                    self.news_direction = direction
                    self.news_entry_price = price
                    self.news_entry_time = datetime.now(pytz.timezone('America/New_York'))
                    self.news_max_exit_time = self.news_entry_time + timedelta(minutes=NEWS_MAX_HOLD_MIN)

                    if direction == 'long':
                        self.news_sl_price = price - NEWS_SL_PTS
                        self.news_tp_price = price + NEWS_TP_PTS
                    else:
                        self.news_sl_price = price + NEWS_SL_PTS
                        self.news_tp_price = price - NEWS_TP_PTS

                    self._trades_log.append({
                        'time': datetime.now().isoformat(),
                        'strategy': 'NEWS',
                        'direction': direction,
                        'entry': price,
                        'sl': self.news_sl_price,
                        'tp': self.news_tp_price,
                        'event': news_row.get('events', ''),
                        'exit': None, 'pnl': None, 'status': 'open'
                    })
                    self._save_state(price)
                    return

        # ── MM20 ENTRY ──
        # Horaires MM20: 16h00 - 20h39 Paris
        if h < self.start_h or (h == self.start_h and m < self.start_m):
            return
        if h > self.close_h or (h == self.close_h and m >= self.close_m):
            return

        # Limites
        if self.trades_today >= MM20_MAX_TRADES_DAY:
            return
        if self.consec_losses >= MM20_DAILY_LOSS_STOP:
            return
        if self.daily_pnl <= -MM20_DAILY_LOSS_USD:
            return

        # Cooldown anti-machine-gunning (5 min entre trades)
        if self.mm20_last_exit_time:
            elapsed = (now_paris - self.mm20_last_exit_time).total_seconds()
            if elapsed < MM20_COOLDOWN_SEC:
                return

        # Refresh barres 1h pour le signal (moins souvent)
        await self.fetch_bars_1h()

        direction = self._check_mm20_entry(price)
        if direction:
            # Calcul trailing stop initial
            trail_stop = self._compute_trail_stop(direction)
            if trail_stop is None:
                return

            MIN_TRAIL_MARGIN = 30  # trail doit etre au moins 30pts de l'entree

            if direction == 'long':
                # Securite: trail stop doit etre SOUS le prix d'entree avec marge
                if trail_stop >= price - MIN_TRAIL_MARGIN:
                    logger.warning("SKIP LONG: trail stop {:.2f} trop proche de entry {:.2f} (marge min {}pts)".format(
                        trail_stop, price, MIN_TRAIL_MARGIN))
                    return
                tp_price = price + MM20_TP_PTS
                sl_price = max(price - MM20_MAX_SL_PTS, trail_stop)
            else:
                # Securite: trail stop doit etre AU-DESSUS du prix d'entree avec marge
                if trail_stop <= price + MIN_TRAIL_MARGIN:
                    logger.warning("SKIP SHORT: trail stop {:.2f} trop proche de entry {:.2f} (marge min {}pts)".format(
                        trail_stop, price, MIN_TRAIL_MARGIN))
                    return
                tp_price = price - MM20_TP_PTS
                sl_price = min(price + MM20_MAX_SL_PTS, trail_stop)

            # SMA20 values for logging
            sma20_5 = self._compute_sma(self.bars_5min, MM20_SMA_PERIOD)
            sma20_1h = self._compute_sma(self.bars_1h, MM20_SMA_PERIOD)
            h1_dist = abs(self.bars_1h[-1]['close'] - sma20_1h) if sma20_1h else 0

            logger.info("MM20 SIGNAL {} @ {:.2f} | SMA5={:.1f} SMA1h={:.1f} h1d={:.0f} | SL={:.2f} TP={:.2f} Trail={:.2f}".format(
                direction.upper(), price, sma20_5 or 0, sma20_1h or 0, h1_dist,
                sl_price, tp_price, trail_stop))

            await self.place_market_order(direction, CONTRACTS)

            self.mm20_in_trade = True
            self.mm20_direction = direction
            self.mm20_entry_price = price
            self.mm20_tp_price = tp_price
            self.mm20_sl_price = sl_price
            self.mm20_trail_stop = trail_stop
            self.mm20_entry_time = now_paris

            self._trades_log.append({
                'time': now_paris.isoformat(),
                'strategy': 'MM20',
                'direction': direction,
                'entry': price,
                'sl': sl_price,
                'tp': tp_price,
                'trail': trail_stop,
                'exit': None, 'pnl': None, 'status': 'open'
            })
            self._save_state(price)

    # ── STATE / HISTORY ────────────────────────────────────

    def _save_state(self, price=None):
        """Sauvegarde l'etat pour le dashboard."""
        try:
            # PnL non-realise
            pnl_pts = 0
            pnl_usd = 0
            active_strategy = None
            active_direction = None
            active_entry = None
            active_sl = None
            active_tp = None

            if self.mm20_in_trade and price:
                active_strategy = 'MM20'
                active_direction = self.mm20_direction
                active_entry = self.mm20_entry_price
                active_sl = self.mm20_trail_stop
                active_tp = self.mm20_tp_price
                if self.mm20_direction == 'long':
                    pnl_pts = price - self.mm20_entry_price
                else:
                    pnl_pts = self.mm20_entry_price - price
                pnl_usd = pnl_pts * TOTAL_PV
            elif self.news_in_trade and price:
                active_strategy = 'NEWS'
                active_direction = self.news_direction
                active_entry = self.news_entry_price
                active_sl = self.news_sl_price
                active_tp = self.news_tp_price
                if self.news_direction == 'long':
                    pnl_pts = price - self.news_entry_price
                else:
                    pnl_pts = self.news_entry_price - price
                pnl_usd = pnl_pts * TOTAL_PV

            state = {
                'timestamp': datetime.now().isoformat(),
                'strategy': 'MM20+NEWS',
                'in_trade': self.mm20_in_trade or self.news_in_trade,
                'active_strategy': active_strategy,
                'direction': active_direction,
                'entry_price': active_entry,
                'sl_price': active_sl,
                'tp_price': active_tp,
                'current_price': price,
                'unrealized_pnl_pts': round(pnl_pts, 2),
                'unrealized_pnl_usd': round(pnl_usd, 2),
                'daily_pnl': round(self.daily_pnl, 2),
                'trades_today': self.trades_today,
                'consec_losses': self.consec_losses,
                'mm20_schedule': "{}h{:02d}-{}h{:02d}".format(
                    self.start_h, self.start_m, self.close_h, self.close_m),
                'trades': self._trades_log,
            }
            STATE_PATH.write_text(json.dumps(state, default=str), encoding='utf-8')
        except Exception as e:
            logger.debug("Erreur sauvegarde etat: {}".format(e))

    def _save_trade_to_history(self, trade):
        """Sauvegarde un trade ferme dans l'historique persistant."""
        try:
            history = []
            if TRADES_HISTORY_PATH.exists():
                history = json.loads(TRADES_HISTORY_PATH.read_text(encoding='utf-8'))
            trade['date'] = str(self.today)
            trade['account_id'] = self.account_id
            trade['instrument'] = INSTRUMENT
            trade['contracts'] = CONTRACTS
            history.append(trade)
            TRADES_HISTORY_PATH.write_text(
                json.dumps(history, default=str, ensure_ascii=False, indent=2),
                encoding='utf-8'
            )
            # Regenere le track record HTML
            self._update_track_record()
        except Exception as e:
            logger.error("Erreur sauvegarde historique: {}".format(e))

    def _update_track_record(self):
        """Regenere le track record HTML apres chaque trade."""
        try:
            from generate_track_record import generate_live_track_record
            generate_live_track_record()
            logger.info("Track record HTML mis a jour")
        except Exception as e:
            logger.warning("Track record update failed: {}".format(e))

    def _save_daily_summary(self):
        """Archive le resume de la journee."""
        if not self._trades_log and self.daily_pnl == 0:
            return
        try:
            summary_path = Path('data/daily_summaries.json')
            summaries = []
            if summary_path.exists():
                summaries = json.loads(summary_path.read_text(encoding='utf-8'))
            summaries.append({
                'date': str(self.today),
                'strategy': 'MM20+NEWS',
                'daily_pnl': round(self.daily_pnl, 2),
                'trades_count': self.trades_today,
                'account_id': self.account_id,
            })
            summary_path.write_text(
                json.dumps(summaries, default=str, ensure_ascii=False, indent=2),
                encoding='utf-8'
            )
        except Exception as e:
            logger.error("Erreur sauvegarde resume: {}".format(e))


async def main():
    logger.info("=" * 60)
    logger.info("MM20 PULLBACK + NEWS NFP/CPI — MNQ x{}".format(CONTRACTS))
    logger.info("MM20: TP={} Trail={}bars MaxSL={} h1d={} DLS={} DailyLoss=${} Cooldown={}s".format(
        MM20_TP_PTS, MM20_TRAIL_BARS, MM20_MAX_SL_PTS, MM20_H1_SMA_DIST, MM20_DAILY_LOSS_STOP, MM20_DAILY_LOSS_USD, MM20_COOLDOWN_SEC))
    logger.info("NEWS: SL={} TP={} lookback={}min move>={}pts".format(
        NEWS_SL_PTS, NEWS_TP_PTS, NEWS_LOOKBACK_MIN, NEWS_MIN_MOVE_PTS))
    logger.info("Daily limit=${}".format(DAILY_LOSS_LIMIT))
    logger.info("=" * 60)

    bot = MM20NewsLive()

    if not bot.account_id or bot.account_id == 0:
        logger.warning("!! AUCUN COMPTE — Selectionnez via http://localhost:8001")

    await bot.connect()

    POLL_INTERVAL = 30  # secondes

    try:
        while True:
            try:
                await bot.check_and_trade()
                try:
                    p = await bot.get_price() if bot.client and bot.contract_id else None
                    bot._save_state(p)
                except Exception:
                    pass
            except Exception as e:
                logger.error("Erreur boucle: {}".format(e))
            await asyncio.sleep(POLL_INTERVAL)
    except KeyboardInterrupt:
        logger.info("Arret MM20+News Live")
        if bot.mm20_in_trade or bot.news_in_trade:
            logger.warning("Position ouverte — fermeture...")
            await bot.close_position()
        if bot.client:
            await bot.client.logout()


if __name__ == '__main__':
    asyncio.run(main())
