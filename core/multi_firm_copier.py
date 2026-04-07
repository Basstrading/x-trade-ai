"""
Multi-Firm Copier — Copy trades across multiple prop firms simultaneously.
===========================================================================
Architecture:
  - 1 master account (any firm) where the trader executes
  - N slave sessions (Topstep, TPT, Tradeify, etc.)
  - Each firm = 1 ProjectXClient session with its own credentials
  - Orders are sent in parallel via asyncio.gather() for minimal latency

All firms use the same Project X API, just different credentials.
"""

import os
import json
import asyncio
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional
from loguru import logger
from projectx_api import ProjectXClient, ConnectionURLS, OrderType, OrderSide


TOPSTEPX_URLS = ConnectionURLS(
    api_endpoint='https://api.topstepx.com',
    user_hub='https://rtc.topstepx.com/hubs/user',
    market_hub='https://rtc.topstepx.com/hubs/market',
)

COPIER_STATE_FILE = Path(__file__).parent.parent / "data" / "copier_state.json"


async def safe_call(func, *args, **kwargs):
    result = func(*args, **kwargs)
    if asyncio.iscoroutine(result):
        return await result
    return result


class FirmSession:
    """Une session connectée à une prop firm."""

    def __init__(self, firm_name: str, username: str, api_key: str,
                 account_ids: List[int]):
        self.firm_name = firm_name
        self.username = username
        self.api_key = api_key
        self.account_ids = account_ids  # Comptes à copier dans cette firm
        self.client: Optional[ProjectXClient] = None
        self.connected = False
        self.accounts: List[dict] = []

    async def connect(self) -> bool:
        """Login à la firm."""
        try:
            self.client = ProjectXClient(TOPSTEPX_URLS)
            await safe_call(self.client.login, {
                "auth_type": "api_key",
                "userName": self.username,
                "apiKey": self.api_key,
            })
            self.connected = True

            # Lister les comptes pour vérifier
            raw = await safe_call(self.client.search_for_account)
            self.accounts = []
            for acc in (raw or []):
                a = acc if isinstance(acc, dict) else getattr(acc, '__dict__', {})
                self.accounts.append({
                    "id": a.get('id', a.get('accountId')),
                    "name": a.get('name', a.get('accountName', '')),
                    "balance": a.get('balance', 0),
                })

            # Vérifier que les account_ids demandés existent
            available = {a['id'] for a in self.accounts}
            valid = [aid for aid in self.account_ids if aid in available]
            if len(valid) != len(self.account_ids):
                missing = set(self.account_ids) - available
                logger.warning(
                    f"{self.firm_name}: comptes introuvables: {missing}"
                )
            self.account_ids = valid

            logger.success(
                f"{self.firm_name}: connecté ({self.username}) — "
                f"{len(self.account_ids)} comptes à copier"
            )
            return True
        except Exception as e:
            logger.error(f"{self.firm_name}: connexion échouée: {e}")
            self.connected = False
            return False

    async def place_order(self, contract_id: str, side: OrderSide,
                          size: int) -> Dict[int, str]:
        """Place l'ordre sur TOUS les comptes de cette firm en parallèle."""
        if not self.connected or not self.client:
            return {aid: "not connected" for aid in self.account_ids}

        async def _place_one(account_id: int):
            try:
                await self.client.place_order(
                    accountId=account_id,
                    contractId=contract_id,
                    type=OrderType.MARKET,
                    side=side,
                    size=size,
                )
                return account_id, "ok"
            except Exception as e:
                return account_id, f"error: {e}"

        results = await asyncio.gather(
            *[_place_one(aid) for aid in self.account_ids],
            return_exceptions=True,
        )

        return {
            r[0] if isinstance(r, tuple) else 0:
            r[1] if isinstance(r, tuple) else str(r)
            for r in results
        }

    async def close_all_positions(self, contract_id: str) -> Dict[int, str]:
        """Ferme les positions sur TOUS les comptes de cette firm en parallèle."""
        if not self.connected or not self.client:
            return {aid: "not connected" for aid in self.account_ids}

        async def _close_one(account_id: int):
            try:
                await self.client.close_position(
                    accountId=account_id,
                    contractId=contract_id,
                )
                return account_id, "ok"
            except Exception as e:
                return account_id, f"error: {e}"

        results = await asyncio.gather(
            *[_close_one(aid) for aid in self.account_ids],
            return_exceptions=True,
        )

        return {
            r[0] if isinstance(r, tuple) else 0:
            r[1] if isinstance(r, tuple) else str(r)
            for r in results
        }

    async def disconnect(self):
        if self.client:
            try:
                await safe_call(self.client.logout)
            except Exception:
                pass
        self.connected = False
        self.client = None

    def to_dict(self):
        return {
            "firm": self.firm_name,
            "username": self.username,
            "api_key": self.api_key,
            "account_ids": self.account_ids,
        }


class MultiFirmCopier:
    """
    Copier multi-firm.

    Usage:
        copier = MultiFirmCopier()
        copier.add_firm("topstep", "Bass123", "key1", [123, 456])
        copier.add_firm("tpt", "Bass123", "key2", [789])
        copier.add_firm("tradeify", "Bass123", "key3", [101112])
        await copier.connect_all()

        # Quand le master trade:
        await copier.copy_order(contract_id, "long", 2)
        await copier.copy_close(contract_id)
    """

    def __init__(self):
        self.firms: Dict[str, FirmSession] = {}
        self.enabled = False
        self._load_state()

    def add_firm(self, firm_name: str, username: str, api_key: str,
                 account_ids: List[int]):
        """Ajoute une firm au copier."""
        self.firms[firm_name] = FirmSession(
            firm_name, username, api_key, account_ids
        )
        self._save_state()
        logger.info(
            f"Copier: ajout {firm_name} — {len(account_ids)} comptes "
            f"({username})"
        )

    def remove_firm(self, firm_name: str):
        """Retire une firm."""
        if firm_name in self.firms:
            del self.firms[firm_name]
            self._save_state()

    async def connect_all(self) -> dict:
        """Connecte toutes les firms en parallèle."""
        if not self.firms:
            return {"error": "Aucune firm configurée"}

        results = await asyncio.gather(
            *[session.connect() for session in self.firms.values()],
            return_exceptions=True,
        )

        status = {}
        for firm_name, result in zip(self.firms.keys(), results):
            if isinstance(result, Exception):
                status[firm_name] = {"connected": False, "error": str(result)}
            else:
                session = self.firms[firm_name]
                status[firm_name] = {
                    "connected": session.connected,
                    "accounts": len(session.account_ids),
                    "account_ids": session.account_ids,
                }

        connected = sum(1 for s in self.firms.values() if s.connected)
        total_accounts = sum(len(s.account_ids) for s in self.firms.values() if s.connected)
        self.enabled = connected > 0

        logger.info(
            f"Copier: {connected}/{len(self.firms)} firms connectées, "
            f"{total_accounts} comptes au total"
        )

        return {"firms": status, "total_accounts": total_accounts}

    async def copy_order(self, contract_id: str, side: str,
                         size: int) -> dict:
        """
        Copie un ordre sur TOUTES les firms + comptes en parallèle.
        C'est ICI que la magie opère : asyncio.gather envoie tout
        en même temps → ~50-200ms de spread max.
        """
        if not self.enabled:
            return {"error": "Copier désactivé"}

        order_side = OrderSide.BUY if side == 'long' else OrderSide.SELL
        t0 = datetime.utcnow()

        # Envoyer à toutes les firms EN PARALLÈLE
        tasks = []
        for firm_name, session in self.firms.items():
            if session.connected:
                tasks.append(
                    session.place_order(contract_id, order_side, size)
                )

        all_results = await asyncio.gather(*tasks, return_exceptions=True)

        elapsed = (datetime.utcnow() - t0).total_seconds() * 1000

        # Compiler les résultats
        results = {}
        firm_names = [n for n, s in self.firms.items() if s.connected]
        total_ok = total_err = 0
        for firm_name, result in zip(firm_names, all_results):
            if isinstance(result, Exception):
                results[firm_name] = {"error": str(result)}
                total_err += 1
            else:
                results[firm_name] = result
                total_ok += sum(1 for v in result.values() if v == "ok")
                total_err += sum(1 for v in result.values() if v != "ok")

        logger.info(
            f"COPY {side.upper()} x{size} → {total_ok} OK, "
            f"{total_err} erreurs | {elapsed:.0f}ms"
        )

        return {
            "side": side,
            "size": size,
            "elapsed_ms": round(elapsed),
            "ok_count": total_ok,
            "error_count": total_err,
            "details": results,
        }

    async def copy_close(self, contract_id: str) -> dict:
        """Ferme les positions sur TOUTES les firms en parallèle."""
        if not self.enabled:
            return {"error": "Copier désactivé"}

        t0 = datetime.utcnow()

        tasks = []
        for firm_name, session in self.firms.items():
            if session.connected:
                tasks.append(session.close_all_positions(contract_id))

        all_results = await asyncio.gather(*tasks, return_exceptions=True)

        elapsed = (datetime.utcnow() - t0).total_seconds() * 1000

        results = {}
        firm_names = [n for n, s in self.firms.items() if s.connected]
        total_ok = total_err = 0
        for firm_name, result in zip(firm_names, all_results):
            if isinstance(result, Exception):
                results[firm_name] = {"error": str(result)}
                total_err += 1
            else:
                results[firm_name] = result
                total_ok += sum(1 for v in result.values() if v == "ok")
                total_err += sum(1 for v in result.values() if v != "ok")

        logger.info(
            f"COPY CLOSE → {total_ok} OK, {total_err} erreurs | "
            f"{elapsed:.0f}ms"
        )

        return {
            "elapsed_ms": round(elapsed),
            "ok_count": total_ok,
            "error_count": total_err,
            "details": results,
        }

    async def disconnect_all(self):
        for session in self.firms.values():
            await session.disconnect()
        self.enabled = False

    def get_status(self) -> dict:
        return {
            "enabled": self.enabled,
            "firms": {
                name: {
                    "connected": s.connected,
                    "username": s.username,
                    "accounts": s.account_ids,
                    "accounts_available": [a['id'] for a in s.accounts],
                }
                for name, s in self.firms.items()
            },
            "total_accounts": sum(
                len(s.account_ids) for s in self.firms.values() if s.connected
            ),
        }

    # ── Persistence ──

    def _save_state(self):
        COPIER_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        state = {
            "firms": {n: s.to_dict() for n, s in self.firms.items()},
            "saved_at": datetime.now().isoformat(),
        }
        COPIER_STATE_FILE.write_text(
            json.dumps(state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _load_state(self):
        if not COPIER_STATE_FILE.exists():
            return
        try:
            state = json.loads(COPIER_STATE_FILE.read_text(encoding="utf-8"))
            for name, data in state.get("firms", {}).items():
                self.firms[name] = FirmSession(
                    firm_name=data["firm"],
                    username=data["username"],
                    api_key=data["api_key"],
                    account_ids=data["account_ids"],
                )
            if self.firms:
                logger.info(
                    f"Copier: {len(self.firms)} firms chargées depuis state"
                )
        except Exception as e:
            logger.warning(f"Copier state load error: {e}")
