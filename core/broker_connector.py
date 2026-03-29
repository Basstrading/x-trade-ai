"""
Broker Connector — Connexion aux brokers prop firm
====================================================
Reutilise le code existant de trading_brain.py.
Supporte ProjectX (Topstep) pour l'instant.
"""

import os
import asyncio
from typing import Dict, List, Optional
from loguru import logger
from projectx_api import ProjectXClient, ConnectionURLS


TOPSTEPX_URLS = ConnectionURLS(
    api_endpoint='https://api.topstepx.com',
    user_hub='https://rtc.topstepx.com/hubs/user',
    market_hub='https://rtc.topstepx.com/hubs/market',
)


async def safe_await(result):
    """Await si c'est une coroutine, sinon retourne tel quel."""
    if asyncio.iscoroutine(result) or asyncio.isfuture(result):
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

    async def connect(self, username: str = "", api_key: str = "") -> dict:
        """Connexion au broker. Retourne {ok, accounts, error}."""
        username = username or os.getenv('PROJECTX_USERNAME', '')
        api_key = api_key or os.getenv('PROJECTX_API_KEY', '')

        if not username or not api_key:
            return {"ok": False, "error": "Username et API key requis"}

        try:
            self.client = ProjectXClient(TOPSTEPX_URLS)
            await safe_await(self.client.login({
                "auth_type": "api_key",
                "userName": username,
                "apiKey": api_key,
            }))
            self.username = username
            self.connected = True

            # Liste les comptes
            raw_accounts = await safe_await(self.client.search_for_account())
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
            raw = await safe_await(self.client.search_for_account())
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
            raw = await safe_await(self.client.search_for_positions(accountId=account_id))
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

    def get_status(self) -> dict:
        return {
            "connected": self.connected,
            "username": self.username,
            "accounts_count": len(self.accounts),
            "accounts": self.accounts,
            "active_account": self.active_account_id,
        }

    async def disconnect(self):
        if self.client:
            try:
                await safe_await(self.client.logout())
            except Exception:
                pass
        self.connected = False
        self.client = None
