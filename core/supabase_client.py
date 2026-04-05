"""
Supabase client for x-trade.ai — multi-tenant data layer.
Handles trades, daily summaries, coaching reports.
"""
import os
import httpx
from datetime import datetime, date, timedelta
from typing import List, Optional
from loguru import logger


SUPABASE_URL = os.getenv(
    'SUPABASE_URL', 'https://yqmjootmvonhqzzmtwij.supabase.co'
)
SUPABASE_SERVICE_KEY = os.getenv(
    'SUPABASE_SERVICE_KEY', ''
)


class SupabaseClient:
    """Client Supabase avec service_role key (bypass RLS)."""

    def __init__(self, url: str = None, key: str = None):
        self.url = url or SUPABASE_URL
        self.key = key or SUPABASE_SERVICE_KEY
        self.headers = {
            'apikey': self.key,
            'Authorization': f'Bearer {self.key}',
            'Content-Type': 'application/json',
            'Prefer': 'return=representation',
        }
        self._client = httpx.AsyncClient(
            base_url=f'{self.url}/rest/v1',
            headers=self.headers,
            timeout=15,
        )

    async def close(self):
        await self._client.aclose()

    # ── Trades ──

    async def upsert_trades(self, user_id: str, broker_account_id: str,
                            trades: List[dict]) -> int:
        """Insert ou update des trades (deduplique par external_id)."""
        if not trades:
            return 0

        rows = []
        for t in trades:
            pnl = t.get('profitAndLoss') or 0
            fees = t.get('fees') or 0
            side = 'short' if t.get('side') == 1 else 'long'
            ts = t.get('creationTimestamp', '')

            # Determiner la session
            session = _detect_session(ts)

            rows.append({
                'user_id': user_id,
                'broker_account_id': broker_account_id,
                'external_id': t.get('id'),
                'instrument': _extract_instrument(t.get('contractId', '')),
                'direction': side,
                'entry_price': t.get('price'),
                'size': t.get('size', 1),
                'pnl': pnl,
                'fees': fees,
                'entry_time': ts,
                'session': session,
            })

        r = await self._client.post(
            '/trades',
            json=rows,
            headers={
                **self.headers,
                'Prefer': 'return=representation,resolution=merge-duplicates',
            },
            params={'on_conflict': 'external_id'},
        )

        if r.status_code in (200, 201):
            inserted = len(r.json()) if r.json() else 0
            logger.info(f"Supabase: {inserted} trades upserted pour user {user_id[:8]}...")
            return inserted
        else:
            logger.error(f"Supabase upsert trades: {r.status_code} {r.text[:200]}")
            return 0

    async def get_trades(self, user_id: str, start_date: str = None,
                         end_date: str = None) -> List[dict]:
        """Recupere TOUS les trades d'un user (pagination auto)."""
        all_trades = []
        page_size = 1000
        offset = 0

        while True:
            params = {
                'user_id': f'eq.{user_id}',
                'order': 'entry_time.asc',
                'limit': str(page_size),
                'offset': str(offset),
            }
            if start_date:
                params['entry_time'] = f'gte.{start_date}'
            if end_date:
                params['entry_time'] = f'lte.{end_date}'

            r = await self._client.get('/trades', params=params)
            if r.status_code != 200:
                logger.error(f"Supabase get_trades: {r.status_code}")
                break

            batch = r.json()
            all_trades.extend(batch)

            if len(batch) < page_size:
                break
            offset += page_size

        return all_trades

    async def get_trades_by_date(self, user_id: str,
                                  trade_date: str) -> List[dict]:
        """Trades d'un jour specifique."""
        start = f'{trade_date}T00:00:00Z'
        end = f'{trade_date}T23:59:59Z'
        return await self.get_trades(user_id, start, end)

    # ── Daily Summaries ──

    async def upsert_daily_summary(self, user_id: str,
                                    broker_account_id: str,
                                    summary: dict) -> bool:
        """Insert ou update un resume journalier."""
        row = {
            'user_id': user_id,
            'broker_account_id': broker_account_id,
            **summary,
        }
        r = await self._client.post(
            '/daily_summaries',
            json=[row],
            headers={
                **self.headers,
                'Prefer': 'return=minimal,resolution=merge-duplicates',
            },
            params={'on_conflict': 'user_id,broker_account_id,trade_date'},
        )
        return r.status_code in (200, 201)

    async def get_daily_summaries(self, user_id: str,
                                   days: int = 30) -> List[dict]:
        """Recupere les N derniers jours."""
        since = (date.today() - timedelta(days=days)).isoformat()
        r = await self._client.get('/daily_summaries', params={
            'user_id': f'eq.{user_id}',
            'trade_date': f'gte.{since}',
            'order': 'trade_date.desc',
        })
        return r.json() if r.status_code == 200 else []

    # ── Coaching Reports ──

    async def save_coaching_report(self, user_id: str,
                                    report: dict) -> Optional[str]:
        """Sauvegarde un rapport du coach."""
        row = {'user_id': user_id, **report}
        r = await self._client.post(
            '/coaching_reports',
            json=[row],
        )
        if r.status_code in (200, 201):
            data = r.json()
            return data[0]['id'] if data else None
        logger.error(f"Supabase save report: {r.status_code} | {r.text[:500]}")
        return None

    async def get_coaching_reports(self, user_id: str,
                                    limit: int = 10) -> List[dict]:
        """Recupere les derniers rapports."""
        r = await self._client.get('/coaching_reports', params={
            'user_id': f'eq.{user_id}',
            'order': 'created_at.desc',
            'limit': str(limit),
        })
        return r.json() if r.status_code == 200 else []

    # ── Broker Accounts ──

    async def get_broker_accounts(self, user_id: str) -> List[dict]:
        """Liste les comptes broker d'un user."""
        r = await self._client.get('/broker_accounts', params={
            'user_id': f'eq.{user_id}',
            'is_active': 'eq.true',
        })
        return r.json() if r.status_code == 200 else []

    async def save_broker_account(self, user_id: str, account: dict) -> bool:
        """Ajoute ou met a jour un compte broker (upsert sur user_id+account_id)."""
        row = {'user_id': user_id, **account}
        r = await self._client.post(
            '/broker_accounts',
            json=[row],
            headers={
                **self.headers,
                'Prefer': 'return=minimal,resolution=merge-duplicates',
            },
            params={'on_conflict': 'user_id,account_id'},
        )
        if r.status_code not in (200, 201):
            logger.error(f"Supabase save broker_account: {r.status_code} {r.text[:200]}")
        return r.status_code in (200, 201)


# ── Helpers ──

def _detect_session(timestamp_str: str) -> str:
    """Detecte la session de trading depuis un timestamp UTC."""
    try:
        if '+' in timestamp_str:
            ts = datetime.fromisoformat(timestamp_str)
        else:
            ts = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
        hour_utc = ts.hour
        # Approximation ET = UTC-4 (EDT)
        hour_et = (hour_utc - 4) % 24

        if 4 <= hour_et < 9:
            return 'london'
        elif 9 <= hour_et < 16:
            return 'new_york'
        elif 20 <= hour_et or hour_et < 4:
            return 'asia'
        else:
            return 'overnight'
    except Exception:
        return 'new_york'


def _extract_instrument(contract_id: str) -> str:
    """Extrait le symbole depuis un contractId Topstep (CON.F.US.MNQ.M26 -> MNQ)."""
    if not contract_id:
        return 'UNKNOWN'
    parts = contract_id.split('.')
    if len(parts) >= 4:
        return parts[3]
    return contract_id
