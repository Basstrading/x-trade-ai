"""
OPR LIVE TRADING — Topstep $50k — MNQ x2
Strategie OPR avec SL dynamique PeriodsHighLow + cap 200pts
SAR = OFF | DST auto-detection
"""

import asyncio
import json
import os
import sys
from datetime import datetime, date, timedelta
from pathlib import Path

sys.path.insert(0, '.')
from dotenv import load_dotenv
load_dotenv()

from loguru import logger
from projectx_api import (
    ProjectXClient, ConnectionURLS,
    OrderSide, OrderType, AggregationUnit
)

from backtester.opr_engine import get_opr_schedule, is_dst_gap, PARIS_TZ

# ── CONFIG ──────────────────────────────────────────────
TOPSTEPX_URLS = ConnectionURLS(
    api_endpoint='https://api.topstepx.com',
    user_hub='https://rtc.topstepx.com/hubs/user',
    market_hub='https://rtc.topstepx.com/hubs/market',
)

# Charge config production
CONFIG_PATH = Path('data/config_production.json')
with open(CONFIG_PATH) as f:
    CONFIG = json.load(f)

PARAMS = CONFIG['params']
LIMITS = CONFIG['topstep_50k']

INSTRUMENT = 'MNQ'
CONTRACTS = PARAMS['contracts']       # 2
POINT_VALUE = PARAMS['point_value']   # 2.0 (MNQ)
DAILY_LOSS_LIMIT = LIMITS['daily_loss_limit']  # -1000
MAX_TRADES = PARAMS['max_trades']     # 6
MAX_LONGS = PARAMS['max_longs']       # 3
MAX_SHORTS = PARAMS['max_shorts']     # 3

TP_LONG = PARAMS['tp_long']           # 217.75
TP_SHORT = PARAMS['tp_short']         # 205.75
SL_TYPE = PARAMS['sl_type']           # periods_high_low
SL_LONG_PERIODS = PARAMS['sl_long_periods']
SL_LONG_DELTA = PARAMS['sl_long_delta']
SL_SHORT_PERIODS = PARAMS['sl_short_periods']
SL_SHORT_DELTA = PARAMS['sl_short_delta']
SL_MAX_PTS = PARAMS['sl_max_pts']     # 200
MIN_RANGE = PARAMS['min_range']       # 15

# Logs
os.makedirs('logs', exist_ok=True)
logger.add("logs/opr_live_{time:YYYY-MM-DD}.log", rotation="1 day", retention="30 days")


OPR_STATE_PATH = Path('data/opr_state.json')
TRADES_HISTORY_PATH = Path('data/trades_history.json')


class OPRLive:
    """Moteur OPR en temps reel."""

    def __init__(self):
        self.client = None
        self.contract_id = None

        # Charge account_id + copy_accounts depuis config_production.json
        self.account_id = None
        self.copy_accounts = []
        try:
            cfg = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
            if cfg.get('account_id'):
                self.account_id = int(cfg['account_id'])
                logger.info(f"Compte master charge depuis config: {self.account_id}")
            self.copy_accounts = [int(x) for x in cfg.get('copy_accounts', [])]
            if self.copy_accounts:
                logger.info(f"Copy trading actif sur {len(self.copy_accounts)} compte(s): {self.copy_accounts}")
        except Exception:
            pass
        if not self.account_id:
            self.account_id = int(os.getenv('ACCOUNT_ID', '0'))

        self._trades_log = []  # Historique des trades pour le dashboard

        # Horaires du jour (recalcules chaque jour)
        self.opr_start_h = 15
        self.opr_start_m = 30
        self.opr_end_h = 15
        self.opr_end_m = 45
        self.close_h = 20
        self.close_m = 49

        # Etat OPR du jour
        self.range_high = None
        self.range_low = None
        self.range_size = 0.0
        self.range_computed = False

        # Etat trading du jour
        self.in_trade = False
        self.direction = ''
        self.entry_price = 0.0
        self.tp_price = 0.0
        self.sl_price = 0.0
        self.sl_pts = 0.0
        self.entry_time = None

        self.daily_pnl = 0.0
        self.trades_today = 0
        self.longs_today = 0
        self.shorts_today = 0
        self.today = None

        # Historique barres 5min (pour SL dynamique)
        self.bars_5min = []

        # Restaure l'état du jour depuis opr_state.json (survit aux restarts)
        self._restore_daily_state()

    def _restore_daily_state(self):
        """Restaure PnL, trades et range depuis opr_state.json (survit aux restarts)."""
        try:
            if not OPR_STATE_PATH.exists():
                return
            saved = json.loads(OPR_STATE_PATH.read_text(encoding='utf-8'))
            # Vérifie que c'est bien le même jour
            saved_ts = saved.get('timestamp', '')
            if not saved_ts:
                return
            from datetime import date as date_type
            saved_date = saved_ts[:10]  # "2026-03-10"
            import pytz
            today_str = datetime.now(pytz.timezone('Europe/Paris')).strftime('%Y-%m-%d')
            if saved_date != today_str:
                logger.info("État sauvegardé d'un autre jour — ignoré")
                return

            self.daily_pnl = saved.get('daily_pnl', 0.0)
            self.trades_today = saved.get('trades_today', 0)
            self.longs_today = saved.get('longs_today', 0)
            self.shorts_today = saved.get('shorts_today', 0)
            self._trades_log = saved.get('trades', [])
            self.range_high = saved.get('range_high')
            self.range_low = saved.get('range_low')
            self.range_size = saved.get('range_size', 0.0)
            self.range_computed = saved.get('range_computed', False)

            # Marque le jour pour que new_day() ne reset pas
            import pytz
            self.today = datetime.now(pytz.timezone('Europe/Paris')).date()

            if self._trades_log or self.daily_pnl != 0:
                logger.info(
                    f"État restauré: {self.trades_today} trades, PnL=${self.daily_pnl:+,.0f}, "
                    f"range={'OK' if self.range_computed else 'non'}"
                )
        except Exception as e:
            logger.debug(f"Restauration état: {e}")

    async def connect(self):
        """Connexion API."""
        self.client = ProjectXClient(TOPSTEPX_URLS)
        await self.client.login({
            'auth_type': 'api_key',
            'userName': os.getenv('PROJECTX_USERNAME'),
            'apiKey': os.getenv('PROJECTX_API_KEY'),
        })

        contracts = await self.client.search_for_contracts(searchText=INSTRUMENT, live=False)
        c = contracts[0]
        self.contract_id = c['id'] if isinstance(c, dict) else c.id
        logger.success(f"Connecte — {INSTRUMENT} id={self.contract_id} compte={self.account_id}")

        # Détecte les positions existantes (reprise après crash)
        try:
            positions = await self.client.search_for_positions(accountId=self.account_id)
            for pos in positions:
                p = pos if isinstance(pos, dict) else pos.__dict__
                cid = p.get('contractId', '')
                if self.contract_id in str(cid):
                    size = p.get('size', 0)
                    avg_price = p.get('averagePrice', 0)
                    ptype = p.get('type', 0)  # 1=long, 2=short
                    direction = 'long' if ptype == 1 else 'short'
                    self.in_trade = True
                    self.direction = direction
                    self.entry_price = avg_price

                    # Restaurer SL/TP depuis opr_state.json si disponible
                    try:
                        saved = json.loads(OPR_STATE_PATH.read_text(encoding='utf-8'))
                        if saved.get('in_trade') and saved.get('direction') == direction:
                            self.sl_price = saved.get('sl_price', avg_price + (41 if direction == 'short' else -41))
                            self.tp_price = saved.get('tp_price', avg_price + (-TP_SHORT if direction == 'short' else TP_LONG))
                            self.sl_pts = saved.get('sl_pts', 41)
                            self.range_high = saved.get('range_high')
                            self.range_low = saved.get('range_low')
                            self.range_size = saved.get('range_size', 0)
                            self.range_computed = saved.get('range_computed', False)
                            logger.info(f"SL/TP restaures depuis état sauvegardé")
                        else:
                            raise ValueError("État non concordant")
                    except Exception:
                        # Fallback: SL/TP par défaut
                        if direction == 'short':
                            self.sl_price = avg_price + SL_MAX_PTS
                            self.tp_price = avg_price - TP_SHORT
                        else:
                            self.sl_price = avg_price - SL_MAX_PTS
                            self.tp_price = avg_price + TP_LONG
                        self.sl_pts = SL_MAX_PTS

                    self._trades_log.append({'time': datetime.now().isoformat(), 'direction': direction, 'entry': avg_price, 'sl': self.sl_price, 'tp': self.tp_price, 'exit': None, 'pnl': None, 'status': 'open'})
                    logger.warning(
                        f"POSITION EXISTANTE DETECTEE: {direction.upper()} x{size} @ {avg_price:.2f} | "
                        f"SL={self.sl_price:.2f} TP={self.tp_price:.2f}"
                    )
                    break
        except Exception as e:
            logger.debug(f"Vérification positions: {e}")

    def new_day(self):
        """Reset journalier + calcul horaires DST."""
        import pytz
        now_paris = datetime.now(pytz.timezone('Europe/Paris'))
        today = now_paris.date()

        if self.today == today:
            return

        # Archive la journée précédente avant reset
        if self.today is not None:
            self._save_daily_summary()

        self.today = today
        self._trades_log = []  # Reset historique jour (l'historique global est dans trades_history.json)
        self.range_high = None
        self.range_low = None
        self.range_size = 0.0
        self.range_computed = False
        self.in_trade = False
        self.daily_pnl = 0.0
        self.trades_today = 0
        self.longs_today = 0
        self.shorts_today = 0
        self.bars_5min = []

        # Horaires DST auto
        sched = get_opr_schedule(today)
        self.opr_start_h, self.opr_start_m = sched[0], sched[1]
        self.opr_end_h, self.opr_end_m = sched[2], sched[3]
        self.close_h, self.close_m = sched[6], sched[7]

        dst_label = "DST (14h30)" if is_dst_gap(today) else "Normal (15h30)"
        logger.info(
            f"=== NOUVEAU JOUR {today} === {dst_label} | "
            f"OPR {self.opr_start_h}h{self.opr_start_m:02d}-{self.opr_end_h}h{self.opr_end_m:02d} | "
            f"Close {self.close_h}h{self.close_m:02d}"
        )

    async def fetch_opr_range(self):
        """Recupere les barres OPR et calcule le range."""
        import pytz
        now_utc = datetime.utcnow()
        start_utc = now_utc - timedelta(hours=2)

        bars = await self.client.retrieve_bars(
            contractId=self.contract_id, live=False,
            startTime=start_utc, endTime=now_utc,
            unit=AggregationUnit.MINUTE, unitNumber=5,
            limit=50, includePartialBar=False,
        )

        if not bars:
            return False

        paris_tz = PARIS_TZ
        opr_bars = []
        for b in bars:
            d = b if isinstance(b, dict) else b.__dict__
            t_str = d.get('t') or d.get('datetime') or d.get('timestamp')
            import pandas as pd
            t = pd.Timestamp(t_str).tz_localize('UTC') if pd.Timestamp(t_str).tz is None else pd.Timestamp(t_str)
            t_paris = t.tz_convert(paris_tz)

            bar_data = {
                'datetime': t_paris,
                'open': float(d.get('o') or d.get('open') or 0),
                'high': float(d.get('h') or d.get('high') or 0),
                'low': float(d.get('l') or d.get('low') or 0),
                'close': float(d.get('c') or d.get('close') or 0),
            }
            self.bars_5min.append(bar_data)

            # Ne prendre que les barres OPR du JOUR ACTUEL (pas d'un autre moment)
            if (t_paris.date() == self.today and
                t_paris.hour == self.opr_start_h and
                self.opr_start_m <= t_paris.minute < self.opr_end_m):
                opr_bars.append(bar_data)

        if len(opr_bars) < 2:
            return False

        self.range_high = max(b['high'] for b in opr_bars)
        self.range_low = min(b['low'] for b in opr_bars)
        self.range_size = self.range_high - self.range_low
        self.range_computed = True

        logger.info(
            f"OPR Range: H={self.range_high:.2f} L={self.range_low:.2f} "
            f"Size={self.range_size:.1f}pts ({len(opr_bars)} barres)"
        )
        return True

    def calc_dynamic_sl(self, direction, entry_price):
        """Calcule le SL dynamique PeriodsHighLow avec cap 200pts."""
        if not self.bars_5min:
            return SL_MAX_PTS

        if direction == 'long':
            periods = SL_LONG_PERIODS
            delta = SL_LONG_DELTA
            recent = self.bars_5min[-periods:] if len(self.bars_5min) >= periods else self.bars_5min
            extreme = min(b['low'] for b in recent)
            sl_pts = entry_price - (extreme + delta)
        else:
            periods = SL_SHORT_PERIODS
            delta = SL_SHORT_DELTA
            recent = self.bars_5min[-periods:] if len(self.bars_5min) >= periods else self.bars_5min
            extreme = max(b['high'] for b in recent)
            sl_pts = (extreme + delta) - entry_price

        # Cap a 200pts
        if sl_pts > SL_MAX_PTS:
            logger.warning(f"SL {sl_pts:.1f}pts > cap {SL_MAX_PTS} -> plafonne")
            sl_pts = SL_MAX_PTS

        if sl_pts <= 0:
            sl_pts = SL_MAX_PTS

        return sl_pts

    def _save_trade_to_history(self, trade: dict):
        """Ajoute un trade fermé à l'historique persistant (ne s'efface jamais)."""
        try:
            history = []
            if TRADES_HISTORY_PATH.exists():
                history = json.loads(TRADES_HISTORY_PATH.read_text(encoding='utf-8'))

            # Enrichit le trade avec le contexte du jour
            trade['date'] = str(self.today)
            trade['account_id'] = self.account_id
            trade['instrument'] = INSTRUMENT
            trade['contracts'] = CONTRACTS
            trade['range_high'] = self.range_high
            trade['range_low'] = self.range_low
            trade['range_size'] = self.range_size

            history.append(trade)
            TRADES_HISTORY_PATH.write_text(
                json.dumps(history, default=str, ensure_ascii=False, indent=2),
                encoding='utf-8'
            )
            logger.info(f"Trade sauvegardé dans l'historique ({len(history)} trades total)")
        except Exception as e:
            logger.error(f"Erreur sauvegarde historique trade: {e}")

    def _save_daily_summary(self):
        """Archive le résumé de la journée avant le reset."""
        if not self._trades_log and self.daily_pnl == 0:
            return  # Rien à archiver
        try:
            summary_path = Path('data/daily_summaries.json')
            summaries = []
            if summary_path.exists():
                summaries = json.loads(summary_path.read_text(encoding='utf-8'))

            summary = {
                'date': str(self.today),
                'daily_pnl': round(self.daily_pnl, 2),
                'trades_count': self.trades_today,
                'longs': self.longs_today,
                'shorts': self.shorts_today,
                'range_high': self.range_high,
                'range_low': self.range_low,
                'range_size': self.range_size,
                'account_id': self.account_id,
            }
            summaries.append(summary)
            summary_path.write_text(
                json.dumps(summaries, default=str, ensure_ascii=False, indent=2),
                encoding='utf-8'
            )
            logger.info(f"Résumé jour archivé: {self.today} PnL=${self.daily_pnl:+,.0f} ({self.trades_today} trades)")
        except Exception as e:
            logger.error(f"Erreur sauvegarde résumé jour: {e}")

    def _save_state(self, price=None):
        """Sauvegarde l'état OPR pour le dashboard."""
        try:
            pnl_pts = 0
            pnl_usd = 0
            if self.in_trade and price and self.entry_price:
                if self.direction == 'long':
                    pnl_pts = price - self.entry_price
                else:
                    pnl_pts = self.entry_price - price
                pnl_usd = pnl_pts * POINT_VALUE * CONTRACTS

            state = {
                'timestamp': datetime.now().isoformat(),
                'in_trade': self.in_trade,
                'direction': getattr(self, 'direction', None),
                'entry_price': getattr(self, 'entry_price', None),
                'sl_price': getattr(self, 'sl_price', None),
                'tp_price': getattr(self, 'tp_price', None),
                'sl_pts': getattr(self, 'sl_pts', None),
                'current_price': price,
                'unrealized_pnl_pts': round(pnl_pts, 2),
                'unrealized_pnl_usd': round(pnl_usd, 2),
                'daily_pnl': round(self.daily_pnl, 2),
                'trades_today': self.trades_today,
                'longs_today': self.longs_today,
                'shorts_today': self.shorts_today,
                'range_high': self.range_high,
                'range_low': self.range_low,
                'range_size': self.range_size,
                'range_computed': self.range_computed,
                'opr_schedule': f"{self.opr_start_h}h{self.opr_start_m:02d}-{self.opr_end_h}h{self.opr_end_m:02d}",
                'trades': getattr(self, '_trades_log', []),
            }
            OPR_STATE_PATH.write_text(json.dumps(state, default=str), encoding='utf-8')
        except Exception as e:
            logger.debug(f"Erreur sauvegarde état OPR: {e}")

    async def get_price(self):
        """Prix actuel via derniere barre 5sec (plus réactif que 1min)."""
        now_utc = datetime.utcnow()
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
        return None

    def reload_copy_accounts(self):
        """Recharge la liste des comptes copies depuis le config (maj via dashboard)."""
        try:
            cfg = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
            self.copy_accounts = [int(x) for x in cfg.get('copy_accounts', [])]
        except Exception:
            pass

    async def place_market_order(self, side: str, size: int):
        """Place un ordre market sur le compte master + comptes copies."""
        order_side = OrderSide.BUY if side == 'long' else OrderSide.SELL

        # Ordre sur le compte master
        order = await self.client.place_order(
            accountId=self.account_id,
            contractId=str(self.contract_id),
            type=OrderType.MARKET,
            side=order_side,
            size=size,
        )
        logger.info(f"ORDRE {side.upper()} x{size} @ MARKET envoye (master {self.account_id})")

        # Copy trading: replique sur les comptes copies
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
                logger.success(f"COPY {side.upper()} x{size} -> compte {copy_id}")
            except Exception as e:
                logger.error(f"COPY ERREUR compte {copy_id}: {e}")

        return order

    async def close_position(self):
        """Ferme la position sur le compte master + comptes copies."""
        # Master
        try:
            await self.client.close_position(
                accountId=self.account_id,
                contractId=str(self.contract_id),
            )
            logger.info(f"Position fermee (master {self.account_id})")
        except Exception as e:
            logger.error(f"Erreur fermeture position master: {e}")
            side = 'short' if self.direction == 'long' else 'long'
            order_side = OrderSide.BUY if side == 'long' else OrderSide.SELL
            await self.client.place_order(
                accountId=self.account_id,
                contractId=str(self.contract_id),
                type=OrderType.MARKET,
                side=order_side,
                size=CONTRACTS,
            )

        # Copy trading: ferme sur les comptes copies
        self.reload_copy_accounts()
        for copy_id in self.copy_accounts:
            try:
                await self.client.close_position(
                    accountId=copy_id,
                    contractId=str(self.contract_id),
                )
                logger.success(f"COPY fermeture -> compte {copy_id}")
            except Exception as e:
                logger.error(f"COPY fermeture ERREUR compte {copy_id}: {e}")

    async def check_and_trade(self):
        """Boucle principale — appelee toutes les ~30s."""
        # Guard: arrêt d'urgence depuis le dashboard
        stop_flag = Path('data/emergency_stop.flag')
        if stop_flag.exists():
            try:
                flag = json.loads(stop_flag.read_text(encoding='utf-8'))
                if flag.get('stopped'):
                    if self.in_trade:
                        logger.warning("ARRET D'URGENCE — fermeture position")
                        price = await self.get_price()
                        await self.close_position()
                        if price and self.entry_price and self._trades_log:
                            pnl_pts = (price - self.entry_price) if self.direction == 'long' else (self.entry_price - price)
                            pnl_usd = pnl_pts * POINT_VALUE * CONTRACTS
                            self.daily_pnl += pnl_usd
                            self._trades_log[-1].update({'exit': price, 'pnl': pnl_usd, 'status': 'emergency'})
                            self._save_trade_to_history(self._trades_log[-1])
                        self.in_trade = False
                    logger.warning("ARRET D'URGENCE ACTIF — trading suspendu. Supprimez data/emergency_stop.flag pour reprendre.")
                    return
            except Exception:
                pass

        # Guard: bot désactivé depuis le dashboard (ON/OFF toggle)
        bot_disabled = Path('data/bot_disabled.flag')
        if bot_disabled.exists():
            return  # Ne pas trader, ne pas fermer les positions existantes

        # Guard: vérifie que la position réelle correspond à l'état interne
        if self.in_trade:
            try:
                positions = await self.client.search_for_positions(accountId=self.account_id)
                has_position = any(
                    str(self.contract_id) in str(p.get('contractId', '') if isinstance(p, dict) else getattr(p, 'contractId', ''))
                    for p in positions
                )
                if not has_position:
                    logger.warning("Position fermée manuellement sur TopstepX — synchronisation")
                    price = await self.get_price()
                    if price and self.entry_price:
                        pnl_pts = (price - self.entry_price) if self.direction == 'long' else (self.entry_price - price)
                        pnl_usd = pnl_pts * POINT_VALUE * CONTRACTS
                        self.daily_pnl += pnl_usd
                        self.trades_today += 1
                        if self.direction == 'long': self.longs_today += 1
                        else: self.shorts_today += 1
                        logger.info(f"MANUAL CLOSE {self.direction.upper()} PnL≈{pnl_pts:+.1f}pts ${pnl_usd:+,.0f}")
                        if self._trades_log:
                            self._trades_log[-1].update({'exit': price, 'pnl': pnl_usd, 'status': 'manual'})
                            self._save_trade_to_history(self._trades_log[-1])
                    self.in_trade = False
            except Exception as e:
                logger.debug(f"Vérification position: {e}")

        # Guard: pas de trading si aucun compte selectionne
        if not self.account_id or self.account_id == 0:
            # Re-check config au cas ou l'utilisateur a selectionne via dashboard
            try:
                cfg = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
                if cfg.get('account_id'):
                    self.account_id = int(cfg['account_id'])
                    logger.success(f"Compte selectionne via dashboard: {self.account_id}")
            except Exception:
                pass
            if not self.account_id or self.account_id == 0:
                logger.warning("!! AUCUN COMPTE SELECTIONNE — Selectionnez un compte via le dashboard. Aucun ordre ne sera passe.")
                return

        import pytz
        self.new_day()

        now_paris = datetime.now(pytz.timezone('Europe/Paris'))
        h, m = now_paris.hour, now_paris.minute

        # Avant OPR start -> rien
        if h < self.opr_start_h or (h == self.opr_start_h and m < self.opr_start_m):
            return

        # Pendant OPR (range en construction)
        if not self.range_computed:
            if h == self.opr_start_h and self.opr_start_m <= m < self.opr_end_m:
                return  # attendre la fin du range

            # Apres OPR end -> calculer le range (mais max 1h après, sinon trop tard)
            minutes_since_opr_end = (h - self.opr_start_h) * 60 + (m - self.opr_end_m)
            if minutes_since_opr_end > 60:
                logger.warning(f"Trop tard pour calculer le range OPR (>{minutes_since_opr_end}min) — skip jour")
                self.range_computed = True
                self.range_high = None
                return

            if h > self.opr_start_h or m >= self.opr_end_m:
                ok = await self.fetch_opr_range()
                if not ok:
                    logger.warning("Range OPR non calculable")
                    return
                if self.range_size < MIN_RANGE:
                    logger.info(f"Range {self.range_size:.1f}pts < min {MIN_RANGE} -> skip jour")
                    self.range_computed = True
                    self.range_high = None
                    return

        if self.range_high is None:
            return

        # Cloture forcee
        force_close = (h > self.close_h or (h == self.close_h and m >= self.close_m))

        if force_close and self.in_trade:
            logger.warning(f"CLOTURE FORCEE {self.close_h}h{self.close_m:02d}")
            price = await self.get_price()
            if price:
                await self.close_position()
                pnl_pts = (price - self.entry_price) if self.direction == 'long' else (self.entry_price - price)
                pnl_usd = pnl_pts * POINT_VALUE * CONTRACTS
                self.daily_pnl += pnl_usd
                self.trades_today += 1
                logger.info(f"TIME EXIT @ {price:.2f} PnL={pnl_pts:+.2f}pts ${pnl_usd:+,.0f} | Jour: ${self.daily_pnl:+,.0f}")
                if self._trades_log:
                    self._trades_log[-1].update({'exit': price, 'pnl': pnl_usd, 'status': 'time'})
                    self._save_trade_to_history(self._trades_log[-1])
            self.in_trade = False
            return

        if force_close:
            return

        # Check limites
        if self.daily_pnl <= DAILY_LOSS_LIMIT:
            if self.in_trade:
                price_now = await self.get_price()
                await self.close_position()
                if price_now and self.entry_price and self._trades_log:
                    pnl_pts = (price_now - self.entry_price) if self.direction == 'long' else (self.entry_price - price_now)
                    pnl_usd = pnl_pts * POINT_VALUE * CONTRACTS
                    self._trades_log[-1].update({'exit': price_now, 'pnl': pnl_usd, 'status': 'daily_limit'})
                    self._save_trade_to_history(self._trades_log[-1])
                self.in_trade = False
            return
        if self.trades_today >= MAX_TRADES:
            return

        price = await self.get_price()
        if not price:
            return

        # === GESTION TRADE OUVERT ===
        if self.in_trade:
            if self.direction == 'long':
                if price >= self.tp_price:
                    await self.close_position()
                    pnl_pts = self.tp_price - self.entry_price
                    pnl_usd = pnl_pts * POINT_VALUE * CONTRACTS
                    self.daily_pnl += pnl_usd
                    self.trades_today += 1
                    self.longs_today += 1
                    logger.success(f"TP LONG @ {price:.2f} PnL={pnl_pts:+.2f}pts ${pnl_usd:+,.0f}")
                    if self._trades_log:
                        self._trades_log[-1].update({'exit': price, 'pnl': pnl_usd, 'status': 'tp'})
                        self._save_trade_to_history(self._trades_log[-1])
                    self.in_trade = False
                elif price <= self.sl_price:
                    await self.close_position()
                    pnl_pts = self.sl_price - self.entry_price  # negatif
                    pnl_usd = pnl_pts * POINT_VALUE * CONTRACTS
                    self.daily_pnl += pnl_usd
                    self.trades_today += 1
                    self.longs_today += 1
                    logger.warning(f"SL LONG @ {price:.2f} PnL={pnl_pts:+.2f}pts ${pnl_usd:+,.0f}")
                    if self._trades_log:
                        self._trades_log[-1].update({'exit': price, 'pnl': pnl_usd, 'status': 'sl'})
                        self._save_trade_to_history(self._trades_log[-1])
                    self.in_trade = False
            else:  # short
                if price <= self.tp_price:
                    await self.close_position()
                    pnl_pts = self.entry_price - self.tp_price
                    pnl_usd = pnl_pts * POINT_VALUE * CONTRACTS
                    self.daily_pnl += pnl_usd
                    self.trades_today += 1
                    self.shorts_today += 1
                    logger.success(f"TP SHORT @ {price:.2f} PnL={pnl_pts:+.2f}pts ${pnl_usd:+,.0f}")
                    if self._trades_log:
                        self._trades_log[-1].update({'exit': price, 'pnl': pnl_usd, 'status': 'tp'})
                        self._save_trade_to_history(self._trades_log[-1])
                    self.in_trade = False
                elif price >= self.sl_price:
                    await self.close_position()
                    pnl_pts = self.entry_price - self.sl_price  # negatif
                    pnl_usd = pnl_pts * POINT_VALUE * CONTRACTS
                    self.daily_pnl += pnl_usd
                    self.trades_today += 1
                    self.shorts_today += 1
                    logger.warning(f"SL SHORT @ {price:.2f} PnL={pnl_pts:+.2f}pts ${pnl_usd:+,.0f}")
                    if self._trades_log:
                        self._trades_log[-1].update({'exit': price, 'pnl': pnl_usd, 'status': 'sl'})
                        self._save_trade_to_history(self._trades_log[-1])
                    self.in_trade = False
            return

        # === RECHERCHE SIGNAL ===
        # Refresh barres 5min pour SL dynamique
        await self.fetch_opr_range()

        # LONG: prix > range_high
        if price > self.range_high and self.longs_today < MAX_LONGS:
            sl_pts = self.calc_dynamic_sl('long', price)
            sl_price = price - sl_pts
            tp_price = price + TP_LONG

            logger.info(
                f"SIGNAL LONG @ {price:.2f} | "
                f"SL={sl_pts:.1f}pts ({sl_price:.2f}) | TP={TP_LONG}pts ({tp_price:.2f})"
            )

            await self.place_market_order('long', CONTRACTS)
            self.in_trade = True
            self.direction = 'long'
            self.entry_price = price
            self.sl_price = sl_price
            self.sl_pts = sl_pts
            self.tp_price = tp_price
            self.entry_time = now_paris
            self._trades_log.append({'time': now_paris.isoformat(), 'direction': 'long', 'entry': price, 'sl': sl_price, 'tp': tp_price, 'exit': None, 'pnl': None, 'status': 'open'})
            self._save_state(price)
            return

        # SHORT: prix < range_low
        if price < self.range_low and self.shorts_today < MAX_SHORTS:
            sl_pts = self.calc_dynamic_sl('short', price)
            sl_price = price + sl_pts
            tp_price = price - TP_SHORT

            logger.info(
                f"SIGNAL SHORT @ {price:.2f} | "
                f"SL={sl_pts:.1f}pts ({sl_price:.2f}) | TP={TP_SHORT}pts ({tp_price:.2f})"
            )

            await self.place_market_order('short', CONTRACTS)
            self.in_trade = True
            self.direction = 'short'
            self.entry_price = price
            self.sl_price = sl_price
            self.sl_pts = sl_pts
            self.tp_price = tp_price
            self.entry_time = now_paris
            self._trades_log.append({'time': now_paris.isoformat(), 'direction': 'short', 'entry': price, 'sl': sl_price, 'tp': tp_price, 'exit': None, 'pnl': None, 'status': 'open'})
            self._save_state(price)
            return


async def main():
    logger.info("=" * 60)
    logger.info("OPR LIVE — Topstep $50k — MNQ x2")
    logger.info(f"TP_L={TP_LONG} TP_S={TP_SHORT} SL_type={SL_TYPE} SL_max={SL_MAX_PTS}")
    logger.info(f"SAR=OFF | Daily limit=${DAILY_LOSS_LIMIT}")
    logger.info(f"Max trades={MAX_TRADES} (L:{MAX_LONGS} S:{MAX_SHORTS})")
    logger.info("=" * 60)

    opr = OPRLive()

    if not opr.account_id or opr.account_id == 0:
        logger.warning("!! AUCUN COMPTE SELECTIONNE — Allez sur http://localhost:8001 pour selectionner un compte")
        logger.warning("Le bot tourne mais ne passera aucun ordre tant qu'un compte n'est pas selectionne")

    await opr.connect()

    POLL_INTERVAL = 30  # secondes

    try:
        while True:
            try:
                await opr.check_and_trade()
                # Sauvegarde état pour le dashboard (même si pas de trade)
                try:
                    p = await opr.get_price() if opr.client and opr.contract_id else None
                    opr._save_state(p)
                except Exception:
                    pass
            except Exception as e:
                logger.error(f"Erreur boucle: {e}")
            await asyncio.sleep(POLL_INTERVAL)
    except KeyboardInterrupt:
        logger.info("Arret OPR Live")
        if opr.in_trade:
            logger.warning("Position ouverte — fermeture...")
            await opr.close_position()
        if opr.client:
            await opr.client.logout()


if __name__ == '__main__':
    asyncio.run(main())
