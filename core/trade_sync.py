"""
Trade Sync — Fetch trades from Project X API and store in Supabase.
Runs per user, called by the API or a cron job.
"""
import httpx
from datetime import datetime, date, timedelta
from collections import defaultdict
from typing import List, Optional
from loguru import logger

from core.supabase_client import SupabaseClient

TOPSTEP_API = 'https://api.topstepx.com'


class TradeSync:
    """Synchronise les trades d'un user depuis Topstep vers Supabase."""

    def __init__(self, supabase: SupabaseClient):
        self.sb = supabase

    async def sync_user(self, user_id: str, username: str, api_key: str,
                        account_id: int, broker_account_id: str,
                        since: str = None) -> dict:
        """
        Sync complet pour un user:
        1. Login Topstep
        2. Fetch trades
        3. Upsert dans Supabase
        4. Calcul des daily summaries
        """
        if not since:
            since = (date.today() - timedelta(days=30)).isoformat()

        # 1. Login
        token = await self._login(username, api_key)
        if not token:
            return {"ok": False, "error": "Login Topstep failed"}

        # 2. Fetch trades
        trades = await self._fetch_trades(token, account_id, since)
        if trades is None:
            return {"ok": False, "error": "Fetch trades failed"}

        logger.info(f"Sync user {user_id[:8]}: {len(trades)} trades depuis {since}")

        # 3. Upsert dans Supabase
        inserted = await self.sb.upsert_trades(user_id, broker_account_id, trades)

        # 4. Calculer et sauvegarder les daily summaries
        summaries = self._compute_daily_summaries(trades)
        for trade_date, summary in summaries.items():
            await self.sb.upsert_daily_summary(
                user_id, broker_account_id, summary
            )

        return {
            "ok": True,
            "trades_fetched": len(trades),
            "trades_upserted": inserted,
            "days": len(summaries),
        }

    async def _login(self, username: str, api_key: str) -> Optional[str]:
        """Login Topstep, retourne le token."""
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.post(f'{TOPSTEP_API}/api/Auth/loginKey', json={
                    'userName': username,
                    'apiKey': api_key,
                })
                data = r.json()
                if data.get('token'):
                    return data['token']
                logger.error(f"Topstep login failed: {data}")
                return None
        except Exception as e:
            logger.error(f"Topstep login error: {e}")
            return None

    async def _fetch_trades(self, token: str, account_id: int,
                            since: str) -> Optional[List[dict]]:
        """Fetch trades depuis Topstep."""
        try:
            async with httpx.AsyncClient(timeout=30) as c:
                r = await c.post(
                    f'{TOPSTEP_API}/api/Trade/search',
                    headers={'Authorization': f'Bearer {token}'},
                    json={
                        'accountId': account_id,
                        'startTimestamp': f'{since}T00:00:00.000Z',
                    },
                )
                if r.status_code == 429:
                    logger.warning("Topstep rate limited")
                    return None
                data = r.json()
                return data.get('trades') or []
        except Exception as e:
            logger.error(f"Topstep fetch trades: {e}")
            return None

    def _compute_daily_summaries(self, trades: List[dict]) -> dict:
        """Calcule les stats par jour depuis les fills bruts."""
        days = defaultdict(lambda: {
            'fills': [], 'pnl': 0.0, 'fees': 0.0,
            'wins': 0, 'losses': 0, 'trades': 0,
        })

        for t in trades:
            ts = t.get('creationTimestamp', '')
            d = ts[:10]
            pnl = t.get('profitAndLoss') or 0
            fees = t.get('fees') or 0
            days[d]['fills'].append(t)
            days[d]['pnl'] += pnl
            days[d]['fees'] += fees
            if pnl > 0:
                days[d]['wins'] += 1
            elif pnl < 0:
                days[d]['losses'] += 1

        result = {}
        for d, info in days.items():
            total_trades = info['wins'] + info['losses']
            net = info['pnl'] - info['fees']
            win_rate = (info['wins'] / total_trades * 100) if total_trades > 0 else 0

            # PnL individuels pour stats
            pnls = [
                (f.get('profitAndLoss') or 0) - (f.get('fees') or 0)
                for f in info['fills']
                if f.get('profitAndLoss') is not None
            ]
            wins_pnl = [p for p in pnls if p > 0]
            losses_pnl = [p for p in pnls if p < 0]

            avg_win = sum(wins_pnl) / len(wins_pnl) if wins_pnl else 0
            avg_loss = sum(losses_pnl) / len(losses_pnl) if losses_pnl else 0
            gross_profit = sum(wins_pnl)
            gross_loss = abs(sum(losses_pnl))
            pf = gross_profit / gross_loss if gross_loss > 0 else 0

            # Peak et drawdown intraday
            running = 0.0
            peak = 0.0
            max_dd = 0.0
            for p in pnls:
                running += p
                if running > peak:
                    peak = running
                dd = running - peak
                if dd < max_dd:
                    max_dd = dd

            # Consecutive wins/losses
            max_cw = max_cl = cw = cl = 0
            for p in pnls:
                if p > 0:
                    cw += 1
                    cl = 0
                elif p < 0:
                    cl += 1
                    cw = 0
                max_cw = max(max_cw, cw)
                max_cl = max(max_cl, cl)

            # Timestamps
            times = [f['creationTimestamp'] for f in info['fills']]
            times.sort()

            result[d] = {
                'trade_date': d,
                'total_trades': total_trades,
                'wins': info['wins'],
                'losses': info['losses'],
                'gross_pnl': round(info['pnl'], 2),
                'fees': round(info['fees'], 2),
                'net_pnl': round(net, 2),
                'peak_pnl': round(peak, 2),
                'max_drawdown': round(max_dd, 2),
                'win_rate': round(win_rate, 1),
                'avg_win': round(avg_win, 2),
                'avg_loss': round(avg_loss, 2),
                'profit_factor': round(pf, 2),
                'largest_win': round(max(pnls) if pnls else 0, 2),
                'largest_loss': round(min(pnls) if pnls else 0, 2),
                'max_consec_wins': max_cw,
                'max_consec_losses': max_cl,
                'first_trade_time': times[0] if times else None,
                'last_trade_time': times[-1] if times else None,
            }

        return result
