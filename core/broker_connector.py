"""
Broker Connector — Connexion aux brokers prop firm
====================================================
Reutilise le code existant de trading_brain.py.
Supporte ProjectX (Topstep) pour l'instant.
"""

import os
import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Callable
from loguru import logger
from projectx_api import ProjectXClient, ConnectionURLS, AggregationUnit


TOPSTEPX_URLS = ConnectionURLS(
    api_endpoint='https://api.topstepx.com',
    user_hub='https://rtc.topstepx.com/hubs/user',
    market_hub='https://rtc.topstepx.com/hubs/market',
)


async def safe_call(func, *args, **kwargs):
    """Appelle une methode qui peut etre sync ou async."""
    result = func(*args, **kwargs)
    if asyncio.iscoroutine(result):
        return await result
    return result


class BrokerConnector:
    """
    Connecteur broker unifie.
    Se connecte, liste les comptes, suit les trades.
    Le Risk Desk ne prend PAS de trades — il observe seulement.
    """

    def __init__(self):
        self.client: Optional[ProjectXClient] = None
        self.connected = False
        self.username = ""
        self.accounts: List[dict] = []
        self.active_account_id: Optional[int] = None
        self.contract_id: Optional[int] = None
        self.instrument: str = ""
        self.current_price: float = 0.0
        self._bar_callback: Optional[Callable] = None
        self._price_callback: Optional[Callable] = None
        self._feed_task: Optional[asyncio.Task] = None

    async def connect(self, username: str = "", api_key: str = "") -> dict:
        """Connexion au broker. Retourne {ok, accounts, error}."""
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
            self.connected = True

            # Liste les comptes
            raw_accounts = await safe_call(self.client.search_for_account)
            self.accounts = []
            for acc in (raw_accounts or []):
                a = acc if isinstance(acc, dict) else getattr(acc, '__dict__', {})
                self.accounts.append({
                    "id": a.get('id', a.get('accountId')),
                    "name": a.get('name', a.get('accountName', '')),
                    "balance": a.get('balance', 0),
                    "can_trade": a.get('canTrade', True),
                })

            logger.success(f"Broker connecte: {username} — {len(self.accounts)} comptes")
            return {"ok": True, "accounts": self.accounts, "username": username}

        except Exception as e:
            logger.error(f"Connexion broker echouee: {e}")
            self.connected = False
            return {"ok": False, "error": str(e)}

    async def get_accounts(self) -> List[dict]:
        """Liste les comptes avec balance a jour."""
        if not self.connected or not self.client:
            return self.accounts
        try:
            raw = await safe_call(self.client.search_for_account)
            self.accounts = []
            for acc in (raw or []):
                a = acc if isinstance(acc, dict) else getattr(acc, '__dict__', {})
                self.accounts.append({
                    "id": a.get('id', a.get('accountId')),
                    "name": a.get('name', a.get('accountName', '')),
                    "balance": a.get('balance', 0),
                    "can_trade": a.get('canTrade', True),
                })
        except Exception as e:
            logger.warning(f"Erreur refresh comptes: {e}")
        return self.accounts

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
                    "direction": 'long' if p.get('type', 0) == 1 else 'short',
                    "avg_price": p.get('averagePrice', 0),
                    "pnl": p.get('profit', p.get('unrealizedPnl', 0)),
                })
            return positions
        except Exception as e:
            logger.warning(f"Erreur positions: {e}")
            return []

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
                    "volume": bar.get('volume', bar.get('v', 0)),
                })
            return result
        except Exception as e:
            logger.warning(f"Erreur fetch bars: {e}")
            return []

    async def fetch_current_price(self) -> float:
        """Recupere le prix actuel via REST."""
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

    def set_callbacks(self, bar_callback: Callable = None, price_callback: Callable = None):
        """Definit les callbacks pour alimenter le Risk Desk."""
        self._bar_callback = bar_callback
        self._price_callback = price_callback

    async def start_market_feed(self, instrument: str = "MNQ", interval: int = 30):
        """
        Demarre le flux de donnees de marche.
        Fetch les bougies 5min et le prix toutes les X secondes.
        Alimente le Risk Desk via les callbacks.
        """
        await self.resolve_contract(instrument)
        if not self.contract_id:
            logger.warning("Market feed: pas de contrat")
            return

        # Charger l'historique initial (50 bougies 5min pour calculer ATR)
        bars = await self.fetch_bars_5min(50)
        if bars and self._bar_callback:
            for bar in bars:
                self._bar_callback(
                    high=bar["high"], low=bar["low"],
                    close=bar["close"], open_p=bar["open"],
                )
            logger.info(f"Market feed: {len(bars)} bougies historiques chargees")

        # Prix initial
        price = await self.fetch_current_price()
        if price and self._price_callback:
            self._price_callback(price)
            logger.info(f"Market feed: prix initial {instrument} = {price}")

        # Boucle de polling
        async def _feed_loop():
            last_bar_count = len(bars) if bars else 0
            while self.connected:
                try:
                    # Prix
                    p = await self.fetch_current_price()
                    if p and self._price_callback:
                        self._price_callback(p)

                    # Nouvelles bougies 5min
                    new_bars = await self.fetch_bars_5min(3)
                    if new_bars and self._bar_callback:
                        for bar in new_bars[-1:]:  # Derniere bougie
                            self._bar_callback(
                                high=bar["high"], low=bar["low"],
                                close=bar["close"], open_p=bar["open"],
                            )
                except Exception as e:
                    logger.debug(f"Feed loop: {e}")

                await asyncio.sleep(interval)

        self._feed_task = asyncio.create_task(_feed_loop())
        logger.success(f"Market feed demarre: {instrument} (polling {interval}s)")

    async def stop_market_feed(self):
        """Arrete le flux."""
        if self._feed_task:
            self._feed_task.cancel()
            self._feed_task = None

    def get_status(self) -> dict:
        return {
            "connected": self.connected,
            "username": self.username,
            "accounts_count": len(self.accounts),
            "accounts": self.accounts,
            "active_account": self.active_account_id,
            "contract_id": self.contract_id,
            "instrument": self.instrument,
            "current_price": self.current_price,
            "feed_active": self._feed_task is not None and not self._feed_task.done(),
        }

    async def disconnect(self):
        if self.client:
            try:
                await safe_call(self.client.logout)
            except Exception:
                pass
        self.connected = False
        self.client = None
