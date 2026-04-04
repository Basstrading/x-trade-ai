"""
Broker Connector — Connexion + suivi des trades en temps reel
===============================================================
1. Se connecte au broker (ProjectX/Topstep)
2. Sauvegarde les credentials (reconnecte auto apres restart)
3. Surveille les trades en live (positions ouvertes/fermees)
4. Alimente le Risk Desk avec chaque trade detecte
5. Flux de prix et bougies pour ATR/vol/choppiness
"""

import os
import json
import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Callable
from loguru import logger
from projectx_api import ProjectXClient, ConnectionURLS, AggregationUnit


TOPSTEPX_URLS = ConnectionURLS(
    api_endpoint='https://api.topstepx.com',
    user_hub='https://rtc.topstepx.com/hubs/user',
    market_hub='https://rtc.topstepx.com/hubs/market',
)

BROKER_STATE_FILE = Path(__file__).parent.parent / "data" / "broker_state.json"


async def safe_call(func, *args, **kwargs):
    """Appelle une methode qui peut etre sync ou async."""
    result = func(*args, **kwargs)
    if asyncio.iscoroutine(result):
        return await result
    return result


class BrokerConnector:
    """
    Connecteur broker avec suivi des trades en temps reel.
    Persiste les credentials et reconnecte automatiquement.
    """

    def __init__(self):
        self.client: Optional[ProjectXClient] = None
        self.connected = False
        self.username = ""
        self._api_key = ""
        self.accounts: List[dict] = []
        self.selected_account_ids: List[int] = []  # Comptes proteges par le Risk Desk
        self.contract_id: Optional[int] = None
        self.instrument: str = ""
        self.current_price: float = 0.0

        # Callbacks vers le Risk Desk
        self._bar_callback: Optional[Callable] = None
        self._price_callback: Optional[Callable] = None
        self._trade_callback: Optional[Callable] = None  # Appele quand un trade est detecte
        self._enforce_callback: Optional[Callable] = None  # Cancel ordres si bloque
        self._sync_callback: Optional[Callable] = None     # Sync balance broker → risk desk
        self._is_blocked: bool = False                        # Flag pour polling adaptatif

        # Taches background
        self._feed_task: Optional[asyncio.Task] = None
        self._trade_monitor_task: Optional[asyncio.Task] = None

        # Etat du trade monitor
        self._last_known_positions: Dict[int, List[dict]] = {}  # account_id -> positions
        self._last_known_balances: Dict[int, float] = {}  # account_id -> balance

    # ── Connexion ──

    async def connect(self, username: str = "", api_key: str = "") -> dict:
        """Connexion au broker. Sauvegarde les credentials."""
        username = username or os.getenv('PROJECTX_USERNAME', '')
        api_key = api_key or os.getenv('PROJECTX_API_KEY', '')

        if not username or not api_key:
            return {"ok": False, "error": "Username et API key requis"}

        try:
            self.client = ProjectXClient(TOPSTEPX_URLS)
            await safe_call(self.client.login, {
                "auth_type": "api_key",
                "userName": username,
                "apiKey": api_key,
            })
            self.username = username
            self._api_key = api_key
            self.connected = True

            # Liste les comptes
            self.accounts = await self._fetch_accounts()

            # Sauvegarde les credentials
            self._save_state()

            logger.success(f"Broker connecte: {username} — {len(self.accounts)} comptes")
            return {"ok": True, "accounts": self.accounts, "username": username}

        except Exception as e:
            logger.error(f"Connexion broker echouee: {e}")
            self.connected = False
            return {"ok": False, "error": str(e)}

    async def auto_reconnect(self):
        """Reconnexion automatique depuis les credentials sauvegardes."""
        state = self._load_state()
        if not state:
            return False

        username = state.get("username", "")
        api_key = state.get("api_key", "")
        self.selected_account_ids = state.get("selected_accounts", [])
        self.instrument = state.get("instrument", "MNQ")

        if not username or not api_key:
            return False

        result = await self.connect(username, api_key)
        if result.get("ok"):
            logger.info(f"Auto-reconnexion: {username} — {len(self.accounts)} comptes")
            return True
        return False

    # ── Comptes ──

    async def _fetch_accounts(self) -> List[dict]:
        """Liste les comptes depuis le broker."""
        if not self.client:
            return []
        try:
            raw = await safe_call(self.client.search_for_account)
            accounts = []
            for acc in (raw or []):
                a = acc if isinstance(acc, dict) else getattr(acc, '__dict__', {})
                accounts.append({
                    "id": a.get('id', a.get('accountId')),
                    "name": a.get('name', a.get('accountName', '')),
                    "balance": a.get('balance', 0),
                    "can_trade": a.get('canTrade', True),
                })
            return accounts
        except Exception as e:
            logger.warning(f"Erreur fetch comptes: {e}")
            return self.accounts

    async def get_accounts(self) -> List[dict]:
        """Rafraichit et retourne les comptes."""
        if self.connected:
            self.accounts = await self._fetch_accounts()
        return self.accounts

    def select_accounts(self, account_ids: List[int]):
        """Selectionne les comptes a proteger par le Risk Desk."""
        self.selected_account_ids = account_ids
        self._save_state()
        logger.info(f"Comptes selectionnes: {account_ids}")

    # ── Suivi des trades en temps reel ──

    async def get_positions(self, account_id: int) -> List[dict]:
        """Positions ouvertes sur un compte."""
        if not self.connected or not self.client:
            return []
        try:
            raw = await safe_call(self.client.search_for_positions, accountId=account_id)
            positions = []
            for pos in (raw or []):
                p = pos if isinstance(pos, dict) else getattr(pos, '__dict__', {})
                positions.append({
                    "contract_id": p.get('contractId', p.get('contract_id')),
                    "size": p.get('size', 0),
                    "type": p.get('type', 0),
                    "direction": 'long' if p.get('type', 0) == 1 else 'short',
                    "avg_price": p.get('averagePrice', 0),
                    "pnl": p.get('profit', p.get('unrealizedPnl', 0)),
                })
            return positions
        except Exception as e:
            logger.debug(f"Erreur positions: {e}")
            return []

    def set_callbacks(self, bar_callback=None, price_callback=None,
                      trade_callback=None, enforce_callback=None,
                      sync_callback=None):
        """Callbacks pour alimenter le Risk Desk."""
        self._bar_callback = bar_callback
        self._price_callback = price_callback
        self._trade_callback = trade_callback
        self._enforce_callback = enforce_callback
        self._sync_callback = sync_callback

    async def start_trade_monitor(self, interval: int = 5):
        """
        Surveille les trades en temps reel.
        Detecte quand une position s'ouvre ou se ferme.
        Appelle le trade_callback avec le PnL quand un trade se ferme.
        """
        if not self.selected_account_ids:
            logger.warning("Trade monitor: aucun compte selectionne")
            return

        # Snapshot initial des positions et balances
        for acc_id in self.selected_account_ids:
            self._last_known_positions[acc_id] = await self.get_positions(acc_id)
            # Trouver la balance de ce compte
            for acc in self.accounts:
                if acc["id"] == acc_id:
                    self._last_known_balances[acc_id] = acc.get("balance", 0)

        async def _monitor_loop():
            while self.connected:
                any_blocked = False
                try:
                    for acc_id in self.selected_account_ids:
                        current_positions = await self.get_positions(acc_id)
                        prev_positions = self._last_known_positions.get(acc_id, [])

                        # Construire les maps par contract_id (ignorer les None)
                        prev_sizes = {
                            p.get("contract_id"): p
                            for p in prev_positions
                            if p.get("size", 0) != 0 and p.get("contract_id")
                        }
                        current_sizes = {
                            p.get("contract_id"): p
                            for p in current_positions
                            if p.get("size", 0) != 0 and p.get("contract_id")
                        }

                        # Sync balance UNE SEULE FOIS avant de traiter les trades
                        # (evite les appels API redondants)
                        new_balance = None
                        if self._sync_callback or prev_sizes != current_sizes:
                            try:
                                accounts = await self._fetch_accounts()
                                for acc in accounts:
                                    if acc["id"] == acc_id:
                                        new_balance = acc.get("balance", 0)
                                        break
                            except Exception as e:
                                logger.debug(f"Fetch accounts: {e}")

                        # Detecter les closes partielles (taille qui diminue)
                        for cid, prev_pos in prev_sizes.items():
                            if cid in current_sizes:
                                cur_pos = current_sizes[cid]
                                prev_size = abs(prev_pos.get("size", 0))
                                cur_size = abs(cur_pos.get("size", 0))
                                if cur_size < prev_size:
                                    logger.info(
                                        f"CLOSE PARTIELLE | {cid} | "
                                        f"{prev_size} -> {cur_size} ct"
                                    )

                        # Positions qui ont disparu → trades fermes
                        # Maj balance AVANT la boucle pour que le PnL soit correct
                        closed_positions = [
                            (cid, prev_pos) for cid, prev_pos in prev_sizes.items()
                            if cid not in current_sizes
                        ]
                        if closed_positions and new_balance is not None:
                            # Calculer le PnL total de TOUTES les closes de ce cycle
                            old_balance = self._last_known_balances.get(acc_id, 0)
                            total_pnl = new_balance - old_balance
                            self._last_known_balances[acc_id] = new_balance

                            if len(closed_positions) == 1:
                                # Un seul trade ferme → PnL complet
                                cid, prev_pos = closed_positions[0]
                                await self._on_trade_closed_with_pnl(
                                    acc_id, prev_pos, total_pnl, new_balance
                                )
                            else:
                                # Plusieurs trades fermes dans le meme cycle
                                # On ne peut pas split le PnL → on attribue au premier
                                # et log un warning
                                logger.warning(
                                    f"MULTI-CLOSE | {len(closed_positions)} trades "
                                    f"fermes en meme temps | PnL total: ${total_pnl:+,.2f}"
                                )
                                for cid, prev_pos in closed_positions:
                                    await self._on_trade_closed_with_pnl(
                                        acc_id, prev_pos,
                                        total_pnl / len(closed_positions),
                                        new_balance,
                                    )

                        # Positions nouvelles → trades ouverts
                        for cid, cur_pos in current_sizes.items():
                            if cid not in prev_sizes:
                                logger.info(
                                    f"TRADE OUVERT | Compte {acc_id} | "
                                    f"{cur_pos['direction']} {cur_pos['size']}ct "
                                    f"@ {cur_pos['avg_price']}"
                                )
                                # Si bloqué → fermer immédiatement la position
                                if self._enforce_callback:
                                    try:
                                        cancelled = await self._enforce_callback(
                                            self.client, acc_id
                                        )
                                        if cancelled == -1:  # Signal: flatten needed
                                            await self.client.close_position(
                                                acc_id, cid
                                            )
                                            logger.critical(
                                                f"ENFORCE FLATTEN | Position {cid} "
                                                f"fermee automatiquement — trading bloque"
                                            )
                                    except Exception as e:
                                        logger.error(f"Enforce flatten: {e}")

                        self._last_known_positions[acc_id] = current_positions

                        # Sync balance broker → risk desk
                        if self._sync_callback and new_balance is not None:
                            try:
                                self._sync_callback(new_balance)
                            except Exception as e:
                                logger.debug(f"Sync callback: {e}")

                        # Enforce risk blocks: cancel pending orders + flatten if blocked
                        if self._enforce_callback:
                            try:
                                result = await self._enforce_callback(self.client, acc_id)
                                if result == -1:
                                    any_blocked = True
                            except Exception as e:
                                logger.debug(f"Enforce callback: {e}")

                    self._is_blocked = any_blocked

                except Exception as e:
                    logger.warning(f"Trade monitor: {e}")

                # Polling adaptatif : 1s si bloqué, 2s sinon
                await asyncio.sleep(1 if self._is_blocked else 2)

        self._trade_monitor_task = asyncio.create_task(_monitor_loop())
        logger.success(
            f"Trade monitor demarre — {len(self.selected_account_ids)} comptes "
            f"(polling adaptatif: 2s normal, 1s si bloque)"
        )

    async def _on_trade_closed_with_pnl(self, account_id: int,
                                        closed_position: dict,
                                        pnl: float, new_balance: float):
        """Appele quand un trade est detecte comme ferme, PnL deja calcule."""
        direction = closed_position.get("direction", "")
        size = closed_position.get("size", 0)
        entry = closed_position.get("avg_price", 0)

        logger.info(
            f"TRADE FERME | Compte {account_id} | "
            f"{direction} {size}ct @ {entry} | "
            f"PnL: ${pnl:+,.2f} | "
            f"Balance: ${new_balance:,.2f}"
        )

        # Appeler le callback du Risk Desk (meme si PnL = 0, c'est un trade reel)
        if self._trade_callback:
            self._trade_callback(
                pnl=pnl,
                direction=direction,
                entry=entry,
                exit_price=0,
                contracts=abs(size),
                reason="broker_detected",
            )

    # ── Market Data ──

    async def resolve_contract(self, instrument: str = "MNQ"):
        """Trouve le contract_id pour un instrument."""
        if not self.connected or not self.client:
            return None
        try:
            self.instrument = instrument
            contracts = await safe_call(self.client.search_for_contracts,
                                        searchText=instrument, live=False)
            if contracts:
                c = contracts[0] if isinstance(contracts[0], dict) else contracts[0].__dict__
                self.contract_id = c.get('id', c.get('contractId'))
                logger.info(f"Contrat {instrument}: ID {self.contract_id}")
                return self.contract_id
        except Exception as e:
            logger.warning(f"Contrat {instrument} non trouve: {e}")
        return None

    async def fetch_bars_5min(self, count: int = 100) -> List[dict]:
        """Recupere les dernieres bougies 5min."""
        if not self.connected or not self.client or not self.contract_id:
            return []
        try:
            now = datetime.utcnow()
            start = now - timedelta(minutes=count * 5)
            bars = await safe_call(self.client.retrieve_bars,
                                   contractId=self.contract_id,
                                   live=False,
                                   startTime=start,
                                   endTime=now,
                                   unit=AggregationUnit.MINUTE,
                                   unitNumber=5,
                                   limit=count,
                                   includePartialBar=False)
            result = []
            for b in (bars or []):
                bar = b if isinstance(b, dict) else getattr(b, '__dict__', {})
                result.append({
                    "open": bar.get('open', bar.get('o', 0)),
                    "high": bar.get('high', bar.get('h', 0)),
                    "low": bar.get('low', bar.get('l', 0)),
                    "close": bar.get('close', bar.get('c', 0)),
                })
            return result
        except Exception as e:
            logger.warning(f"Erreur fetch bars: {e}")
            return []

    async def fetch_current_price(self) -> float:
        """Recupere le prix actuel."""
        if not self.connected or not self.client or not self.contract_id:
            return self.current_price
        try:
            now = datetime.utcnow()
            bars = await safe_call(self.client.retrieve_bars,
                                   contractId=self.contract_id,
                                   live=False,
                                   startTime=now - timedelta(seconds=30),
                                   endTime=now,
                                   unit=AggregationUnit.SECOND,
                                   unitNumber=5,
                                   limit=5,
                                   includePartialBar=True)
            if bars:
                last = bars[-1] if isinstance(bars[-1], dict) else bars[-1].__dict__
                self.current_price = last.get('close', last.get('c', 0))
            return self.current_price
        except Exception as e:
            logger.debug(f"Erreur prix: {e}")
            return self.current_price

    async def start_market_feed(self, instrument: str = "MNQ", interval: int = 30):
        """Demarre le flux prix + bougies. Alimente le Risk Desk."""
        await self.resolve_contract(instrument)
        if not self.contract_id:
            logger.warning("Market feed: pas de contrat")
            return

        # Historique initial (50 bougies pour ATR)
        bars = await self.fetch_bars_5min(50)
        if bars and self._bar_callback:
            for bar in bars:
                self._bar_callback(
                    high=bar["high"], low=bar["low"],
                    close=bar["close"], open_p=bar["open"],
                )
            logger.info(f"Market feed: {len(bars)} bougies chargees")

        # Prix initial
        price = await self.fetch_current_price()
        if price and self._price_callback:
            self._price_callback(price)

        # Boucle polling
        async def _feed_loop():
            while self.connected:
                try:
                    p = await self.fetch_current_price()
                    if p and self._price_callback:
                        self._price_callback(p)

                    new_bars = await self.fetch_bars_5min(3)
                    if new_bars and self._bar_callback:
                        self._bar_callback(
                            high=new_bars[-1]["high"], low=new_bars[-1]["low"],
                            close=new_bars[-1]["close"], open_p=new_bars[-1]["open"],
                        )
                except Exception as e:
                    logger.debug(f"Feed: {e}")
                await asyncio.sleep(interval)

        self._feed_task = asyncio.create_task(_feed_loop())
        logger.success(f"Market feed: {instrument} (polling {interval}s)")

    # ── Persistence ──

    def _save_state(self):
        """Sauvegarde credentials + comptes selectionnes."""
        BROKER_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        state = {
            "username": self.username,
            "api_key": self._api_key,
            "selected_accounts": self.selected_account_ids,
            "instrument": self.instrument,
            "saved_at": datetime.now().isoformat(),
        }
        BROKER_STATE_FILE.write_text(
            json.dumps(state, ensure_ascii=False), encoding="utf-8"
        )

    def _load_state(self) -> Optional[dict]:
        """Charge les credentials sauvegardes."""
        if not BROKER_STATE_FILE.exists():
            return None
        try:
            return json.loads(BROKER_STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return None

    # ── Status ──

    def get_status(self) -> dict:
        return {
            "connected": self.connected,
            "username": self.username,
            "accounts_count": len(self.accounts),
            "accounts": self.accounts,
            "selected_accounts": self.selected_account_ids,
            "contract_id": self.contract_id,
            "instrument": self.instrument,
            "current_price": self.current_price,
            "feed_active": self._feed_task is not None and not self._feed_task.done(),
            "trade_monitor_active": self._trade_monitor_task is not None and not self._trade_monitor_task.done(),
        }

    async def disconnect(self):
        if self._feed_task:
            self._feed_task.cancel()
        if self._trade_monitor_task:
            self._trade_monitor_task.cancel()
        if self.client:
            try:
                await safe_call(self.client.logout)
            except Exception:
                pass
        self.connected = False
        self.client = None
