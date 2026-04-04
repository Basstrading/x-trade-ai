"""
API FastAPI — Interface REST + WebSocket pour le Trading Agent
Port 8001
"""

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, BackgroundTasks, Depends, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from loguru import logger
import asyncio
import json
import os
import hashlib
import secrets
import traceback
import uvicorn
from pathlib import Path
from datetime import datetime, timedelta

app = FastAPI(title="Trading Agent D6", version="1.0")

# ── Auth ──
# RISK_DESK_ADMIN_KEY dans .env — requis pour acceder au panneau admin
# Le trader n'a PAS besoin de cle, il voit uniquement le framework
ADMIN_KEY = os.getenv("RISK_DESK_ADMIN_KEY", "")
if not ADMIN_KEY:
    # Genere une cle au premier lancement et l'affiche
    ADMIN_KEY = secrets.token_urlsafe(16)
    logger.warning(f"RISK_DESK_ADMIN_KEY non defini dans .env — cle generee: {ADMIN_KEY}")
    logger.warning("Ajoutez RISK_DESK_ADMIN_KEY={} dans votre .env".format(ADMIN_KEY))


def require_admin(request: Request):
    """Verifie que la requete contient la cle admin."""
    # Check header
    key = request.headers.get("X-Admin-Key", "")
    # Check query param (pour le dashboard HTML)
    if not key:
        key = request.query_params.get("admin_key", "")
    # Check cookie
    if not key:
        key = request.cookies.get("admin_key", "")

    import hmac
    if not hmac.compare_digest(key, ADMIN_KEY):
        raise HTTPException(status_code=403, detail="Acces refuse — cle admin requise")
    return True

# Global state
_brain = None
_ws_clients = set()
_backtest_status = {"running": False, "progress": "", "step": 0}


def create_app(brain):
    global _brain
    _brain = brain
    return app


# --- Static files ---
static_dir = os.path.join(os.path.dirname(__file__), 'static')
landing_dir = os.path.join(os.path.dirname(__file__), '..', 'RiskGuardian', 'web')
app.mount("/static", StaticFiles(directory=static_dir), name="static")
app.mount("/landing", StaticFiles(directory=landing_dir), name="landing")


@app.get("/")
async def landing_page():
    """Landing page x-trade.ai — le client arrive ici."""
    return FileResponse(os.path.join(landing_dir, "index.html"))


@app.get("/dashboard")
async def dashboard():
    """Ancien dashboard trading D6."""
    return FileResponse(os.path.join(static_dir, "dashboard.html"))


@app.get("/journal")
async def journal_page():
    """Journal de trading + Coach IA."""
    return FileResponse(os.path.join(static_dir, "journal.html"))


@app.get("/api/journal")
async def journal_data():
    """Donnees du journal + analyse coach pour le user connecte."""
    try:
        from core.supabase_client import SupabaseClient
        from core.trading_coach import TradingCoach

        sb = SupabaseClient(
            key=os.getenv('SUPABASE_SERVICE_KEY', '')
        )
        coach = TradingCoach(sb)

        # Pour l'instant, user_id fixe (sera remplace par auth Supabase)
        user_id = os.getenv(
            'XTRADE_USER_ID',
            'bcdfb9ee-3297-4307-9534-7306c0ce2246'
        )

        result = await coach.analyze(user_id)
        await sb.close()

        return result
    except Exception as e:
        return {"error": str(e)}


@app.get("/owner")
async def owner_page(admin_key: str = ""):
    """Owner panel — gestion business, licences, codes promo."""
    if admin_key != ADMIN_KEY:
        return FileResponse(os.path.join(static_dir, "risk-desk-login.html"))
    return FileResponse(os.path.join(static_dir, "owner.html"))


@app.get("/robots.txt")
async def robots_txt():
    return FileResponse(os.path.join(static_dir, "robots.txt"), media_type="text/plain")


@app.get("/sitemap.xml")
async def sitemap_xml():
    return FileResponse(os.path.join(static_dir, "sitemap.xml"), media_type="application/xml")


@app.get("/llms.txt")
async def llms_txt():
    return FileResponse(os.path.join(static_dir, "llms.txt"), media_type="text/plain")


@app.get("/blog")
async def blog_index():
    return FileResponse(os.path.join(static_dir, "blog", "index.html"))


@app.get("/blog/{slug}")
async def blog_post(slug: str):
    path = os.path.join(static_dir, "blog", f"{slug}.html")
    if os.path.exists(path):
        return FileResponse(path)
    return FileResponse(os.path.join(static_dir, "blog", "index.html"))


@app.get("/onboarding")
async def onboarding_page():
    """Page d'onboarding — le client configure son Risk Desk en 4 clics."""
    return FileResponse(os.path.join(static_dir, "onboarding.html"))


@app.get("/risk-desk")
async def risk_desk_trader_page():
    """Dashboard trader — feu vert/rouge, taille, stop."""
    return FileResponse(os.path.join(static_dir, "risk-desk-trader.html"))


@app.get("/risk-desk/admin")
async def risk_desk_admin_page(admin_key: str = ""):
    """Dashboard admin — panneau de controle AUTO/MANUEL. Necessite admin_key."""
    if admin_key != ADMIN_KEY:
        return FileResponse(os.path.join(static_dir, "risk-desk-login.html"))
    return FileResponse(os.path.join(static_dir, "risk-desk-admin.html"))


@app.get("/api/status")
async def get_status():
    if _brain:
        return await _brain.get_status()
    return {"connected": False, "error": "Brain not initialized"}


@app.get("/api/opr")
async def get_opr_state():
    """P&L et trades du jour — lu depuis trades_history.json + mm20_state.json."""
    today = datetime.now().strftime('%Y-%m-%d')
    history_path = Path('data/trades_history.json')
    mm20_path = Path('data/mm20_state.json')

    trades_today = []
    daily_pnl = 0.0

    # Trades depuis l'historique
    try:
        if history_path.exists():
            all_trades = json.loads(history_path.read_text(encoding='utf-8'))
            trades_today = [t for t in all_trades if t.get('date') == today]
            daily_pnl = sum(t.get('pnl', 0) + t.get('fees', 0) for t in trades_today)
    except Exception:
        pass

    # Etat live du bot MM20 (position ouverte)
    in_trade = False
    unrealized_pnl_usd = 0.0
    try:
        if mm20_path.exists():
            mm20 = json.loads(mm20_path.read_text(encoding='utf-8'))
            in_trade = mm20.get('in_trade', False)
            unrealized_pnl_usd = mm20.get('unrealized_pnl_usd', 0)
    except Exception:
        pass

    return {
        "daily_pnl": round(daily_pnl, 2),
        "trades_today": len(trades_today),
        "trades": trades_today,
        "in_trade": in_trade,
        "unrealized_pnl_usd": round(unrealized_pnl_usd, 2),
    }


@app.get("/api/market")
async def get_market():
    if _brain and _brain.analyzer:
        try:
            summary = await _brain.analyzer.get_summary()
            return summary
        except Exception as e:
            return {"error": str(e)}
    return {"error": "Analyzer not available"}


@app.get("/api/signal")
async def get_signal():
    if _brain and _brain.aggregator:
        try:
            signal = await _brain.aggregator.get_aggregated_signal()
            return signal
        except Exception as e:
            return {"error": str(e)}
    return {"error": "Aggregator not available"}


@app.get("/api/risk")
async def get_risk():
    if _brain:
        return _brain.risk.get_status()
    return {"error": "Risk manager not available"}


@app.post("/api/risk/account")
async def set_account_type(account_type: str):
    """Change le type de compte Topstep. account_type: 25k/50k/100k/150k/300k"""
    if not _brain:
        return {"error": "Brain not initialized"}
    try:
        from core.risk_manager import RiskManager, AgentRiskRules, get_account_config
        config = get_account_config(account_type)
        _brain.risk = RiskManager(
            account_type=account_type,
            agent_rules=AgentRiskRules(
                stop_fb_points=_brain.strategy_params['stop_fb'],
                stop_br_points=_brain.strategy_params['stop_br'],
                trail_step_points=_brain.strategy_params['trail_step'],
                exit_fb_mode=_brain.strategy_params['exit_fb'],
            ),
        )
        logger.info(f"Compte change : {config.name}")
        return {
            'status': 'ok',
            'account': config.name,
            'daily_limit_topstep': config.daily_loss_limit,
            'daily_limit_agent': _brain.risk.agent_daily_limit,
            'trailing_dd': config.trailing_drawdown,
            'max_contracts': config.max_contracts,
        }
    except ValueError as e:
        return {'error': str(e)}


# ==================================================================
# COMPTES PROJECTX
# ==================================================================

@app.get("/api/accounts")
async def get_accounts():
    """Liste tous les comptes ProjectX avec solde et statut."""
    if not _brain or not _brain.client:
        return {"error": "Non connecte", "accounts": []}

    try:
        raw = await _brain.client.search_for_account(onlyActiveAccounts=True)
    except Exception as e:
        return {"error": str(e), "accounts": []}

    # Charge le compte selectionne et comptes copies depuis config
    selected_id = None
    copy_accounts = []
    cfg_path = Path(os.path.dirname(os.path.dirname(__file__))) / 'data' / 'config_production.json'
    if cfg_path.exists():
        try:
            cfg = json.loads(cfg_path.read_text(encoding='utf-8'))
            selected_id = cfg.get('account_id')
            copy_accounts = cfg.get('copy_accounts', [])
        except Exception:
            pass

    accounts = []
    for acc in raw:
        a = acc if isinstance(acc, dict) else acc.__dict__
        acc_id = a.get('id') or a.get('accountId')
        name = a.get('name') or a.get('accountName') or ''
        balance = a.get('balance') or a.get('accountBalance') or 0
        active = a.get('isActive', a.get('active', True))

        # Determine le type Topstep depuis le nom (150K AVANT 50K pour eviter faux match)
        name_upper = name.upper()
        ts_type = ''
        if '150K' in name_upper:
            ts_type = '150k'
        elif '300K' in name_upper:
            ts_type = '300k'
        elif '100K' in name_upper:
            ts_type = '100k'
        elif '50K' in name_upper:
            ts_type = '50k'
        elif '25K' in name_upper:
            ts_type = '25k'
        elif 'EXPRESS' in name_upper:
            ts_type = 'express'

        # Filtre: ne garder que les comptes Topstep (TC/EXPRESS), pas les simules (S1DEC, etc.)
        if not ts_type:
            continue

        accounts.append({
            'id': acc_id,
            'name': name,
            'balance': float(balance) if balance else 0,
            'active': bool(active),
            'topstep_type': ts_type,
            'selected': acc_id == selected_id,
            'copying': acc_id in copy_accounts,
            'connected': True,
        })

    return {"accounts": accounts, "selected_id": selected_id, "copy_accounts": copy_accounts}


# ==================================================================
# BAL / MLL (Topstep Account Limits)
# ==================================================================

_PEAK_BAL_FILE = Path(os.path.dirname(os.path.dirname(__file__))) / 'data' / 'peak_balance.json'

# Topstep trailing drawdown per account type
_TOPSTEP_TRAIL_DD = {
    '25k': 1500, '50k': 2000, '100k': 3000, '150k': 4500, '300k': 7500,
}


def _get_topstep_type(name: str) -> str:
    n = name.upper()
    for t in ['150K', '300K', '100K', '50K', '25K']:
        if t in n:
            return t.lower()
    return ''


def _load_peak_balances() -> dict:
    if _PEAK_BAL_FILE.exists():
        try:
            return json.loads(_PEAK_BAL_FILE.read_text(encoding='utf-8'))
        except Exception:
            pass
    return {}


def _save_peak_balances(data: dict):
    _PEAK_BAL_FILE.write_text(json.dumps(data, indent=2), encoding='utf-8')


@app.get("/api/account-info")
async def get_account_info():
    """Retourne BAL, MLL et buffer pour le compte master."""
    if not _brain or not _brain.client:
        return {"error": "Non connecte"}

    # Charge le compte selectionne
    cfg_path = Path(os.path.dirname(os.path.dirname(__file__))) / 'data' / 'config_production.json'
    selected_id = None
    if cfg_path.exists():
        try:
            cfg = json.loads(cfg_path.read_text(encoding='utf-8'))
            selected_id = cfg.get('account_id')
        except Exception:
            pass

    if not selected_id:
        return {"error": "Aucun compte selectionne"}

    try:
        raw = await _brain.client.search_for_account(onlyActiveAccounts=True)
    except Exception as e:
        return {"error": str(e)}

    # Trouve le compte master
    master = None
    for acc in raw:
        a = acc if isinstance(acc, dict) else acc.__dict__
        aid = a.get('id') or a.get('accountId')
        if aid == selected_id:
            master = a
            break

    if not master:
        return {"error": "Compte master non trouve"}

    balance = float(master.get('balance') or master.get('accountBalance') or 0)
    name = master.get('name') or ''
    ts_type = _get_topstep_type(name)
    trail_dd = _TOPSTEP_TRAIL_DD.get(ts_type, 2000)

    # Load peak balance (Topstep MLL trails EOD only, not intraday)
    peaks = _load_peak_balances()
    key = str(selected_id)
    peak = peaks.get(key, balance)

    # If no peak stored yet, initialize from balance
    if key not in peaks:
        peaks[key] = balance
        _save_peak_balances(peaks)

    mll = peak - trail_dd
    buffer = balance - mll

    return {
        "bal": round(balance, 2),
        "mll": round(mll, 2),
        "buffer": round(buffer, 2),
        "peak": round(peak, 2),
        "trail_dd": trail_dd,
        "topstep_type": ts_type,
        "account_id": selected_id,
        "account_name": name,
    }


@app.post("/api/account-info/set-mll")
async def set_mll(mll: float):
    """Permet de forcer le MLL manuellement (recalcule le peak)."""
    cfg_path = Path(os.path.dirname(os.path.dirname(__file__))) / 'data' / 'config_production.json'
    selected_id = None
    if cfg_path.exists():
        try:
            cfg = json.loads(cfg_path.read_text(encoding='utf-8'))
            selected_id = cfg.get('account_id')
        except Exception:
            pass

    if not selected_id:
        return {"error": "Aucun compte selectionne"}

    # Determine le type de compte
    name = cfg.get('account_name', '')
    ts_type = _get_topstep_type(name)
    trail_dd = _TOPSTEP_TRAIL_DD.get(ts_type, 2000)

    # peak = mll + trail_dd
    peak = mll + trail_dd
    peaks = _load_peak_balances()
    peaks[str(selected_id)] = peak
    _save_peak_balances(peaks)

    return {"status": "ok", "mll": mll, "peak": peak, "trail_dd": trail_dd}


@app.post("/api/accounts/select")
async def select_account(account_id: int):
    """Selectionne un compte pour le trading. Sauvegarde dans config."""
    if not _brain:
        return {"error": "Brain not initialized"}

    # Verifie que le compte existe
    try:
        raw = await _brain.client.search_for_account(onlyActiveAccounts=False)
    except Exception as e:
        return {"error": str(e)}

    found = None
    for acc in raw:
        a = acc if isinstance(acc, dict) else acc.__dict__
        aid = a.get('id') or a.get('accountId')
        if aid == account_id:
            found = a
            break

    if not found:
        return {"error": f"Compte {account_id} non trouve"}

    # Met a jour le brain
    _brain.account_id = account_id

    # Sauvegarde dans config_production.json
    cfg_path = Path(os.path.dirname(os.path.dirname(__file__))) / 'data' / 'config_production.json'
    try:
        cfg = {}
        if cfg_path.exists():
            cfg = json.loads(cfg_path.read_text(encoding='utf-8'))
        cfg['account_id'] = account_id
        cfg['account_name'] = found.get('name') or found.get('accountName') or ''
        cfg_path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding='utf-8')
    except Exception as e:
        logger.error(f"Erreur sauvegarde config: {e}")

    name = found.get('name') or found.get('accountName') or ''
    logger.info(f"Compte selectionne: {account_id} — {name}")

    return {
        "status": "ok",
        "account_id": account_id,
        "account_name": name,
    }


@app.post("/api/accounts/copy")
async def toggle_copy_account(account_id: int):
    """Active/desactive la copie des trades sur un compte. Sauvegarde dans config."""
    cfg_path = Path(os.path.dirname(os.path.dirname(__file__))) / 'data' / 'config_production.json'
    try:
        cfg = {}
        if cfg_path.exists():
            cfg = json.loads(cfg_path.read_text(encoding='utf-8'))

        copy_accounts = cfg.get('copy_accounts', [])
        enabled = False

        if account_id in copy_accounts:
            copy_accounts.remove(account_id)
            logger.info(f"Copy DESACTIVE pour compte {account_id}")
        else:
            # Ne pas copier sur le compte principal (master)
            if account_id == cfg.get('account_id'):
                return {"error": "Impossible de copier sur le compte principal (master)"}
            copy_accounts.append(account_id)
            enabled = True
            logger.info(f"Copy ACTIVE pour compte {account_id}")

        cfg['copy_accounts'] = copy_accounts
        cfg_path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding='utf-8')

        return {"status": "ok", "account_id": account_id, "copying": enabled, "copy_accounts": copy_accounts}
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/strategies")
async def get_strategies():
    if _brain and _brain.strategies:
        return _brain.strategies.get_strategies_info()
    return []


@app.get("/api/options")
async def get_options_levels():
    """Retourne les niveaux options du jour depuis hubtrading.fr DB."""
    if not _brain:
        return {"error": "Brain not initialized"}

    if not _brain.options_levels and _brain.options_reader:
        try:
            _brain.options_levels = _brain.options_reader.get_today_levels()
        except Exception:
            pass

    if not _brain.options_levels:
        return {"error": "Options non disponibles -- verifier HUBTRADING_DB_URL"}

    ol = _brain.options_levels
    return {
        "date": str(ol.date) if ol.date else None,
        "gamma_wall_up": ol.gamma_wall_up,
        "gamma_wall_down": ol.gamma_wall_down,
        "hvl": ol.hvl,
        "call_wall": ol.call_wall,
        "put_wall": ol.put_wall,
        "zero_gamma": ol.zero_gamma,
        "market_bias": ol.market_bias,
        "gamma_condition": ol.gamma_condition,
        "pc_oi": ol.pc_oi,
        "qscore_option": ol.qscore_option,
        "key_levels": ol.get_key_levels(),
        "raw_data": {k: str(v) for k, v in (ol.raw_data or {}).items()},
    }


@app.get("/api/trades/history")
async def get_trades_history(days: int = 30):
    """Historique complet des trades (persistant)."""
    history_path = Path('data/trades_history.json')
    try:
        if history_path.exists():
            trades = json.loads(history_path.read_text(encoding='utf-8'))
            # Filtre par nombre de jours si demandé
            if days and days < 365:
                cutoff = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
                trades = [t for t in trades if (t.get('date', '') or '') >= cutoff]
            return {"trades": trades, "total": len(trades)}
    except Exception as e:
        return {"error": str(e)}
    return {"trades": [], "total": 0}


@app.get("/api/equity")
async def get_equity_curve():
    """Equity curve persistante — reconstruite depuis trades_history.json."""
    history_path = Path('data/trades_history.json')
    opr_path = Path('data/opr_state.json')

    try:
        points = []
        cumul = 0.0

        if history_path.exists():
            for t in json.loads(history_path.read_text(encoding='utf-8')):
                pnl = (t.get('pnl') or 0) + (t.get('fees') or 0)
                if pnl == 0 and t.get('status') == 'open':
                    continue
                cumul += pnl
                points.append({
                    'pnl': round(pnl, 2),
                    'cumulative': round(cumul, 2),
                    'date': t.get('date', ''),
                    'direction': t.get('direction', ''),
                })

        unrealized = 0.0
        if opr_path.exists():
            opr = json.loads(opr_path.read_text(encoding='utf-8'))
            if opr.get('in_trade') and opr.get('unrealized_pnl_usd'):
                unrealized = opr['unrealized_pnl_usd']

        return {
            "points": points,
            "total_pnl": round(cumul, 2),
            "unrealized": round(unrealized, 2),
            "equity_now": round(cumul + unrealized, 2),
        }
    except Exception as e:
        return {"error": str(e), "points": []}


@app.get("/api/trades/summary")
async def get_daily_summaries():
    """Résumés journaliers (PnL par jour)."""
    summary_path = Path('data/daily_summaries.json')
    try:
        if summary_path.exists():
            summaries = json.loads(summary_path.read_text(encoding='utf-8'))
            return {"summaries": summaries}
    except Exception as e:
        return {"error": str(e)}
    return {"summaries": []}


BOT_DISABLE_FLAG = Path('data/bot_disabled.flag')

@app.get("/api/bot/status")
async def bot_status():
    """Vérifie si le bot est activé ou désactivé."""
    disabled = BOT_DISABLE_FLAG.exists()
    stopped = Path('data/emergency_stop.flag').exists()
    return {"enabled": not disabled and not stopped}

@app.post("/api/bot/enable")
async def bot_enable():
    """Active le bot — supprime tous les flags de blocage."""
    if BOT_DISABLE_FLAG.exists():
        BOT_DISABLE_FLAG.unlink()
    stop_path = Path('data/emergency_stop.flag')
    if stop_path.exists():
        stop_path.unlink()
    logger.info("BOT ACTIVE — trading autorisé")
    return {"enabled": True}

@app.post("/api/bot/disable")
async def bot_disable():
    """Désactive le bot — écrit un flag de blocage (ne ferme pas les positions)."""
    BOT_DISABLE_FLAG.write_text(json.dumps({
        "disabled": True,
        "timestamp": __import__('datetime').datetime.now().isoformat()
    }), encoding='utf-8')
    logger.warning("BOT DESACTIVE — aucun trade ne sera pris")
    return {"enabled": False}

@app.post("/api/start")
async def start_trading():
    # Supprime les flags de blocage
    if BOT_DISABLE_FLAG.exists():
        BOT_DISABLE_FLAG.unlink()
    stop_path = Path('data/emergency_stop.flag')
    if stop_path.exists():
        stop_path.unlink()
        logger.info("Flags de blocage supprimés — trading peut reprendre")

    if _brain:
        success = await _brain.start_trading()
        return {"success": success, "active": _brain.is_active}
    return {"success": False, "error": "Brain not initialized"}


@app.post("/api/stop")
async def stop_trading():
    # Écrit un flag d'arrêt que opr_live.py lit aussi
    try:
        stop_path = Path('data/emergency_stop.flag')
        stop_path.write_text(json.dumps({
            "stopped": True,
            "timestamp": __import__('datetime').datetime.now().isoformat(),
            "reason": "Arrêt d'urgence dashboard"
        }), encoding='utf-8')
        logger.warning("ARRET D'URGENCE — flag écrit pour opr_live.py")
    except Exception as e:
        logger.error(f"Erreur écriture flag stop: {e}")

    if _brain:
        success = await _brain.stop_trading()
        return {"success": success, "active": _brain.is_active}
    return {"success": False, "error": "Brain not initialized"}


# ==================================================================
# BACKTEST ROUTES
# ==================================================================

@app.get("/api/track-record")
async def get_track_record():
    """Sert le track record HTML live."""
    tr_path = Path('data/track_record_nasdaq.html')
    if tr_path.exists():
        return FileResponse(tr_path, media_type='text/html')
    return {"error": "Track record non genere"}


@app.post("/api/track-record/refresh")
async def refresh_track_record(background_tasks: BackgroundTasks):
    """Regenere le track record HTML depuis trades_history.json."""
    def _gen():
        try:
            from generate_track_record import generate_live_track_record
            generate_live_track_record()
        except Exception as e:
            logger.error("Track record generation failed: {}".format(e))
    background_tasks.add_task(_gen)
    return {"status": "started"}


@app.post("/api/backtest/run")
async def run_backtest(background_tasks: BackgroundTasks, days: int = 90):
    """Lance un backtest en arriere-plan."""
    global _backtest_status

    if _backtest_status["running"]:
        return {"error": "Backtest deja en cours"}

    _backtest_status = {"running": True, "progress": "Demarrage...", "step": 0}
    background_tasks.add_task(_backtest_task, days)
    return {"status": "started", "days": days}


@app.get("/api/backtest/status")
async def get_backtest_status():
    return _backtest_status


@app.get("/api/backtest/last")
async def get_last_backtest():
    data_dir = Path(os.path.dirname(os.path.dirname(__file__))) / 'data'
    f = data_dir / 'last_backtest.json'
    if f.exists():
        try:
            return json.loads(f.read_text(encoding='utf-8'))
        except Exception as e:
            return {"error": str(e)}
    return {"error": "Aucun backtest disponible"}


_optimize_status = {"running": False, "progress": ""}


@app.post("/api/backtest/optimize")
async def run_optimization(background_tasks: BackgroundTasks, days: int = 30):
    """Lance l'optimisation des parametres en arriere-plan."""
    global _optimize_status

    if _optimize_status["running"]:
        return {"error": "Optimisation deja en cours"}

    _optimize_status = {"running": True, "progress": "Demarrage..."}
    background_tasks.add_task(_optimize_task, days)
    return {"status": "started", "days": days}


@app.get("/api/backtest/optimize/status")
async def get_optimize_status():
    return _optimize_status


@app.get("/api/backtest/optimize/last")
async def get_last_optimization():
    data_dir = Path(os.path.dirname(os.path.dirname(__file__))) / 'data'
    f = data_dir / 'optimization.json'
    if f.exists():
        try:
            return json.loads(f.read_text(encoding='utf-8'))
        except Exception as e:
            return {"error": str(e)}
    return {"error": "Aucune optimisation disponible"}


async def _optimize_task(days: int = 30):
    """Execute l'optimisation en arriere-plan."""
    global _optimize_status
    try:
        import sys
        import pandas as pd
        base_dir = os.path.dirname(os.path.dirname(__file__))
        if base_dir not in sys.path:
            sys.path.insert(0, base_dir)

        from backtester.engine import BacktestOptimizer, BacktestEngine

        _optimize_status = {"running": True, "progress": f"Recuperation {days}j de donnees..."}
        await broadcast({"type": "optimize_progress", "message": _optimize_status["progress"]})

        # Fetch data (same logic as backtest)
        df_1min = None
        df_5min = None

        if _brain and _brain.client and _brain.contract_id:
            from projectx_api import AggregationUnit
            now = datetime.utcnow()

            all_1min = []
            all_5min = []
            n_chunks = (days // 7) + 1

            for chunk_i in range(n_chunks):
                chunk_end = now - timedelta(days=chunk_i * 7)
                chunk_start = chunk_end - timedelta(days=7)

                _optimize_status["progress"] = f"Chargement semaine {chunk_i+1}/{n_chunks}..."
                await broadcast({"type": "optimize_progress", "message": _optimize_status["progress"]})

                try:
                    bars = await _brain.client.retrieve_bars(
                        contractId=_brain.contract_id, live=False,
                        startTime=chunk_start, endTime=chunk_end,
                        unit=AggregationUnit.MINUTE, unitNumber=1,
                        limit=10000, includePartialBar=False
                    )
                    if bars:
                        all_1min.extend(bars)
                except Exception as e:
                    logger.warning(f"Opt chunk 1min {chunk_i}: {e}")

                try:
                    bars5 = await _brain.client.retrieve_bars(
                        contractId=_brain.contract_id, live=False,
                        startTime=chunk_start, endTime=chunk_end,
                        unit=AggregationUnit.MINUTE, unitNumber=5,
                        limit=5000, includePartialBar=False
                    )
                    if bars5:
                        all_5min.extend(bars5)
                except Exception as e:
                    logger.warning(f"Opt chunk 5min {chunk_i}: {e}")

            if all_1min:
                def bars_to_df(bars):
                    data = []
                    for b in bars:
                        d = b if isinstance(b, dict) else b.__dict__
                        dt = d.get('t') or d.get('timestamp') or d.get('datetime')
                        data.append({
                            'datetime': dt,
                            'open': float(d.get('o') or d.get('open') or 0),
                            'high': float(d.get('h') or d.get('high') or 0),
                            'low': float(d.get('l') or d.get('low') or 0),
                            'close': float(d.get('c') or d.get('close') or 0),
                            'volume': float(d.get('v') or d.get('volume') or 1),
                        })
                    df = pd.DataFrame(data)
                    df['datetime'] = pd.to_datetime(df['datetime'], utc=True)
                    df = df.sort_values('datetime').drop_duplicates('datetime')
                    return df.set_index('datetime')

                df_1min = bars_to_df(all_1min)
                df_5min = bars_to_df(all_5min)

        if df_1min is None or len(df_1min) < 500:
            # Synthetic fallback
            import numpy as np
            np.random.seed(42)
            n = days * 390
            price = 24000.0
            prices = [price]
            for _ in range(n - 1):
                price += np.random.randn() * 5
                prices.append(max(price, 20000))
            dates = pd.date_range(datetime.now() - timedelta(days=days), periods=n, freq='1min')
            df_1min = pd.DataFrame({
                'open': prices,
                'high': [p + abs(np.random.randn() * 3) for p in prices],
                'low': [p - abs(np.random.randn() * 3) for p in prices],
                'close': [p + np.random.randn() * 2 for p in prices],
                'volume': np.random.randint(100, 1000, n).astype(float),
            }, index=dates)
            n5 = n // 5
            dates_5 = pd.date_range(dates[0], periods=n5, freq='5min')
            df_5min = pd.DataFrame({
                'open': prices[::5][:n5],
                'high': [p + abs(np.random.randn() * 8) for p in prices[::5][:n5]],
                'low': [p - abs(np.random.randn() * 8) for p in prices[::5][:n5]],
                'close': [p + np.random.randn() * 4 for p in prices[::5][:n5]],
                'volume': np.random.randint(500, 5000, n5).astype(float),
            }, index=dates_5)

        _optimize_status["progress"] = f"{len(df_1min)} barres. Optimisation ({5*4*4*5}=400 combos)..."
        await broadcast({"type": "optimize_progress", "message": _optimize_status["progress"]})

        # Options
        options_levels = None
        if _brain and _brain.options_levels:
            ol = _brain.options_levels
            options_levels = {
                'hvl': ol.hvl, 'call_wall': ol.call_wall,
                'put_wall': ol.put_wall, 'gamma_wall_up': ol.gamma_wall_up,
                'gamma_wall_down': ol.gamma_wall_down,
            }

        # Run optimizer
        optimizer = BacktestOptimizer()
        results = optimizer.run_optimization(df_1min, df_5min, options_levels)

        top10 = results[:10]

        # Save
        data_dir = Path(os.path.dirname(os.path.dirname(__file__))) / 'data'
        data_dir.mkdir(exist_ok=True)
        (data_dir / 'optimization.json').write_text(
            json.dumps(top10, default=str, ensure_ascii=False), encoding='utf-8'
        )

        # Run best params backtest and save
        best_params = top10[0]['params'] if top10 else None
        if best_params:
            from dataclasses import asdict
            engine = BacktestEngine(params=best_params)
            report = engine.run(df_1min, df_5min, options_levels)
            if report:
                report_dict = asdict(report)
                report_dict['best_params'] = best_params
                (data_dir / 'last_backtest.json').write_text(
                    json.dumps(report_dict, default=str, ensure_ascii=False), encoding='utf-8'
                )

        _optimize_status = {"running": False,
                            "progress": f"Termine: {len(results)} combos valides, best PF={top10[0]['profit_factor'] if top10 else 0}"}
        await broadcast({
            "type": "optimize_complete",
            "results": top10,
            "best_params": best_params,
        })

    except Exception as e:
        tb = traceback.format_exc()
        logger.error(f"Optimize erreur: {e}\n{tb}")
        _optimize_status = {"running": False, "progress": f"Erreur: {str(e)[:200]}"}
        await broadcast({"type": "optimize_error", "error": str(e), "traceback": tb})


async def _backtest_task(days: int = 90):
    """Execute le backtest en arriere-plan."""
    global _backtest_status
    try:
        import sys
        import pandas as pd
        base_dir = os.path.dirname(os.path.dirname(__file__))
        if base_dir not in sys.path:
            sys.path.insert(0, base_dir)

        from backtester.engine import BacktestEngine

        _backtest_status = {"running": True, "step": 1,
                            "progress": f"Recuperation {days} jours de donnees..."}

        await broadcast({"type": "backtest_progress", "step": 1,
                         "message": f"Recuperation {days} jours de donnees..."})

        # Retrieve historical data via projectx-api
        if _brain and _brain.client and _brain.contract_id:
            from projectx_api import AggregationUnit
            now = datetime.utcnow()

            # Fetch in reverse 7-day chunks (most recent first)
            all_1min = []
            all_5min = []
            n_chunks = (days // 7) + 1

            for chunk_i in range(n_chunks):
                chunk_end = now - timedelta(days=chunk_i * 7)
                chunk_start = chunk_end - timedelta(days=7)

                _backtest_status["progress"] = \
                    f"Chargement semaine {chunk_i + 1}/{n_chunks}..."
                await broadcast({"type": "backtest_progress", "step": 1,
                                 "message": _backtest_status["progress"]})

                try:
                    bars = await _brain.client.retrieve_bars(
                        contractId=_brain.contract_id, live=False,
                        startTime=chunk_start, endTime=chunk_end,
                        unit=AggregationUnit.MINUTE, unitNumber=1,
                        limit=10000, includePartialBar=False
                    )
                    if bars:
                        all_1min.extend(bars)
                except Exception as e:
                    logger.warning(f"Chunk 1min {chunk_i} erreur: {e}")

                try:
                    bars5 = await _brain.client.retrieve_bars(
                        contractId=_brain.contract_id, live=False,
                        startTime=chunk_start, endTime=chunk_end,
                        unit=AggregationUnit.MINUTE, unitNumber=5,
                        limit=5000, includePartialBar=False
                    )
                    if bars5:
                        all_5min.extend(bars5)
                except Exception as e:
                    logger.warning(f"Chunk 5min {chunk_i} erreur: {e}")

            if not all_1min:
                _backtest_status = {"running": False, "step": -1,
                                    "progress": "Erreur: aucune donnee 1min recuperee"}
                await broadcast({"type": "backtest_error",
                                 "error": "Aucune donnee 1min recuperee"})
                return

            def bars_to_df(bars):
                data = []
                for b in bars:
                    d = b if isinstance(b, dict) else b.__dict__
                    dt = d.get('timestamp') or d.get('t') or d.get('datetime') or d.get('time')
                    data.append({
                        'datetime': dt,
                        'open': float(d.get('open') or d.get('o') or 0),
                        'high': float(d.get('high') or d.get('h') or 0),
                        'low': float(d.get('low') or d.get('l') or 0),
                        'close': float(d.get('close') or d.get('c') or 0),
                        'volume': float(d.get('volume') or d.get('v') or 1),
                    })
                df = pd.DataFrame(data)
                df['datetime'] = pd.to_datetime(df['datetime'], utc=True)
                df = df.sort_values('datetime').drop_duplicates('datetime')
                df = df.set_index('datetime')
                return df

            df_1min = bars_to_df(all_1min)
            df_5min = bars_to_df(all_5min)

        else:
            # No brain connection — generate synthetic data for testing
            import numpy as np
            np.random.seed(42)
            n = days * 390
            price = 24000.0
            prices = [price]
            for _ in range(n - 1):
                price += np.random.randn() * 5
                prices.append(max(price, 20000))

            dates = pd.date_range(datetime.now() - timedelta(days=days), periods=n, freq='1min')
            df_1min = pd.DataFrame({
                'open': prices,
                'high': [p + abs(np.random.randn() * 3) for p in prices],
                'low': [p - abs(np.random.randn() * 3) for p in prices],
                'close': [p + np.random.randn() * 2 for p in prices],
                'volume': np.random.randint(100, 1000, n).astype(float),
            }, index=dates)

            n5 = len(dates) // 5
            dates_5 = pd.date_range(dates[0], periods=n5, freq='5min')
            df_5min = pd.DataFrame({
                'open': prices[::5][:n5],
                'high': [p + abs(np.random.randn() * 8) for p in prices[::5][:n5]],
                'low': [p - abs(np.random.randn() * 8) for p in prices[::5][:n5]],
                'close': [p + np.random.randn() * 4 for p in prices[::5][:n5]],
                'volume': np.random.randint(500, 5000, n5).astype(float),
            }, index=dates_5)

        _backtest_status = {"running": True, "step": 2,
                            "progress": f"{len(df_1min)} barres 1min, {len(df_5min)} barres 5min. Calcul..."}
        await broadcast({"type": "backtest_progress", "step": 2,
                         "message": _backtest_status["progress"]})

        # Options levels
        options_levels = None
        if _brain and _brain.options_levels:
            ol = _brain.options_levels
            options_levels = {
                'hvl': ol.hvl,
                'call_wall': ol.call_wall,
                'put_wall': ol.put_wall,
                'gamma_wall_up': ol.gamma_wall_up,
                'gamma_wall_down': ol.gamma_wall_down,
            }

        # Run backtest
        engine = BacktestEngine()
        report = engine.run(df_1min, df_5min, options_levels)

        if report is None:
            _backtest_status = {"running": False, "step": -1,
                                "progress": "Aucun trade genere"}
            await broadcast({"type": "backtest_error", "error": "Aucun trade genere"})
            return

        # Save report
        from dataclasses import asdict
        report_dict = asdict(report)

        data_dir = Path(os.path.dirname(os.path.dirname(__file__))) / 'data'
        data_dir.mkdir(exist_ok=True)
        (data_dir / 'last_backtest.json').write_text(
            json.dumps(report_dict, default=str, ensure_ascii=False), encoding='utf-8'
        )

        _backtest_status = {"running": False, "step": 3,
                            "progress": f"Termine: {report.total_trades} trades, WR {report.win_rate}%"}
        await broadcast({"type": "backtest_complete", "report": report_dict})

    except Exception as e:
        tb = traceback.format_exc()
        logger.error(f"Backtest erreur: {e}\n{tb}")
        _backtest_status = {"running": False, "step": -1,
                            "progress": f"Erreur: {str(e)[:200]}"}
        await broadcast({"type": "backtest_error", "error": str(e), "traceback": tb})


# ==================================================================
# RISK DESK — Framework temps reel (daytrading discretionnaire)
# ==================================================================

_risk_desk = None


def create_risk_desk(desk):
    """Attache le Risk Desk Engine a l'API."""
    global _risk_desk
    _risk_desk = desk
    logger.info("Risk Desk Engine connecte a l'API")


@app.get("/api/risk-desk/framework")
async def risk_desk_framework():
    """
    CADRE DE TRADING — ce que le trader voit en temps reel.

    Retourne le cadre en vigueur MAINTENANT :
    - allowed: peut-on trader ? (true/false)
    - contracts: taille de position pour le prochain trade
    - stop_distance_pts: distance du stop en points (ATR-based)
    - max_loss_per_trade: perte max en $ si le stop est touche
    - risk_budget_remaining: budget risque restant en $
    - trades_remaining: nombre de trades restants

    Le trader regarde ca et execute. Pas de validation trade par trade.
    """
    if not _risk_desk:
        return {"error": "Risk Desk non initialise", "allowed": False}
    return _risk_desk.get_framework().to_dict()


@app.post("/api/risk-desk/record-trade")
async def risk_desk_record_trade(
    pnl: float,
    direction: str = "",
    entry: float = 0,
    exit_price: float = 0,
    reason: str = "",
    contracts: int = 1,
):
    """
    Enregistre un trade clos. Le framework se recalcule automatiquement.

    Retourne le nouveau cadre en vigueur apres ce trade.
    """
    if not _risk_desk:
        return {"error": "Risk Desk non initialise"}
    fw = _risk_desk.record_trade(pnl, direction, entry, exit_price, reason, contracts)
    return fw.to_dict()


@app.get("/api/risk-desk/status")
async def risk_desk_status():
    """Statut rapide du Risk Desk."""
    if not _risk_desk:
        return {"online": False, "error": "Risk Desk non initialise"}
    return _risk_desk.get_status()


@app.get("/api/risk-desk/admin")
async def risk_desk_admin_view(auth=Depends(require_admin)):
    """Vue admin/risk manager — tous les details. Le trader n'y a pas acces."""
    if not _risk_desk:
        return {"error": "Risk Desk non initialise"}
    return _risk_desk.get_admin_view()


@app.get("/api/risk-desk/profile")
async def risk_desk_profile():
    """Profil de risque verrouille du trader."""
    if not _risk_desk:
        return {"error": "Risk Desk non initialise"}
    return _risk_desk.profile.to_dict()


@app.post("/api/risk-desk/kill")
async def risk_desk_kill(auth=Depends(require_admin)):
    """KILL SWITCH — Coupe toutes les positions immediatement."""
    if not _risk_desk:
        return {"error": "Risk Desk non initialise"}
    if not _brain or not _brain.client:
        return {"error": "Non connecte au broker"}

    result = await _risk_desk.kill_all(_brain.client, _brain.account_id)
    return result


@app.get("/api/risk-desk/payout")
async def risk_desk_payout():
    """Progression vers le payout."""
    if not _risk_desk:
        return {"error": "Risk Desk non initialise"}
    return _risk_desk.state.get_payout_status()


@app.get("/api/risk-desk/stats")
async def risk_desk_stats():
    """Stats globales du trader."""
    if not _risk_desk:
        return {"error": "Risk Desk non initialise"}
    return _risk_desk.state.get_stats()


@app.get("/api/risk-desk/history")
async def risk_desk_history():
    """Historique jour par jour."""
    if not _risk_desk:
        return {"error": "Risk Desk non initialise"}
    return _risk_desk.state.get_daily_history()


@app.get("/api/risk-desk/today")
async def risk_desk_today():
    """Trades du jour avec trade_log complet."""
    if not _risk_desk:
        return {"error": "Risk Desk non initialise"}
    return _risk_desk.state.get_today()


@app.get("/api/risk-desk/trades")
async def risk_desk_all_trades():
    """Tous les trades enregistres (persistant)."""
    if not _risk_desk:
        return {"error": "Risk Desk non initialise"}
    return {"trades": _risk_desk.state._all_trades, "total": len(_risk_desk.state._all_trades)}


@app.get("/api/risk-desk/equity")
async def risk_desk_equity():
    """Equity curve depuis tous les trades enregistres."""
    if not _risk_desk:
        return {"error": "Risk Desk non initialise"}
    trades = _risk_desk.state._all_trades
    initial = _risk_desk.state.state.initial_balance or _risk_desk.state.state.account_size
    points = [{"x": 0, "balance": round(initial, 2), "timestamp": "", "pnl": 0}]
    balance = initial
    for i, t in enumerate(trades):
        pnl = t.get("pnl", 0)
        balance += pnl
        points.append({
            "x": i + 1,
            "balance": round(balance, 2),
            "timestamp": t.get("timestamp", ""),
            "pnl": round(pnl, 2),
            "direction": t.get("direction", ""),
            "contracts": t.get("contracts", 0),
        })
    return {
        "initial_balance": round(initial, 2),
        "current_balance": round(balance, 2),
        "total_profit": round(balance - initial, 2),
        "total_trades": len(trades),
        "curve": points,
    }


@app.get("/api/risk-desk/plans")
async def risk_desk_available_plans():
    """Liste toutes les prop firms et plans disponibles."""
    from core.prop_firm_rules import list_available_plans
    return list_available_plans()


@app.get("/api/risk-desk/instruments")
async def risk_desk_instruments():
    """Liste tous les instruments supportes."""
    from core.institutional_risk_manager import INSTRUMENTS
    return {
        sym: {
            "name": spec.name,
            "exchange": spec.exchange,
            "tick_size": spec.tick_size,
            "tick_value": spec.tick_value,
            "point_value": spec.point_value,
            "asset_class": spec.asset_class,
        }
        for sym, spec in INSTRUMENTS.items()
    }


# ==================================================================
# RISK DESK ADMIN — Panneau de controle (AUTO/MANUEL)
# ==================================================================

@app.get("/api/risk-desk/session-config")
async def risk_desk_session_config(auth=Depends(require_admin)):
    """Etat complet du session config (tous les params AUTO/MANUEL)."""
    if not _risk_desk:
        return {"error": "Risk Desk non initialise"}
    return _risk_desk.session_config.to_dict()


@app.post("/api/risk-desk/param/{param_name}/auto")
async def risk_desk_param_auto(param_name: str, auth=Depends(require_admin)):
    """Passe un parametre en mode AUTO."""
    if not _risk_desk:
        return {"error": "Risk Desk non initialise"}
    param = _get_param(param_name)
    if not param:
        return {"error": f"Parametre inconnu: {param_name}"}
    param.set_auto()
    return {"ok": True, "param": param.to_dict()}


@app.post("/api/risk-desk/param/{param_name}/manual")
async def risk_desk_param_manual(param_name: str, value: float, auth=Depends(require_admin)):
    """Passe un parametre en mode MANUEL avec une valeur."""
    if not _risk_desk:
        return {"error": "Risk Desk non initialise"}
    param = _get_param(param_name)
    if not param:
        return {"error": f"Parametre inconnu: {param_name}"}
    param.set_manual(value)
    return {"ok": True, "param": param.to_dict()}


@app.post("/api/risk-desk/block")
async def risk_desk_block(reason: str = "Bloque par le risk manager", auth=Depends(require_admin)):
    """Bloque manuellement le trader."""
    if not _risk_desk:
        return {"error": "Risk Desk non initialise"}
    _risk_desk.session_config.block_trader(reason)
    return {"ok": True, "blocked": True, "reason": reason}


@app.post("/api/risk-desk/unblock")
async def risk_desk_unblock(auth=Depends(require_admin)):
    """Debloque le trader."""
    if not _risk_desk:
        return {"error": "Risk Desk non initialise"}
    _risk_desk.session_config.unblock_trader()
    return {"ok": True, "blocked": False}


@app.post("/api/risk-desk/global-reduction")
async def risk_desk_global_reduction(mult: float, reason: str = "", auth=Depends(require_admin)):
    """Force une reduction globale de taille (ex: 0.5 = 50%)."""
    if not _risk_desk:
        return {"error": "Risk Desk non initialise"}
    _risk_desk.session_config.set_global_reduction(mult, reason)
    return {"ok": True, "mult": mult, "reason": reason}


@app.post("/api/risk-desk/clear-reduction")
async def risk_desk_clear_reduction(auth=Depends(require_admin)):
    """Annule la reduction globale."""
    if not _risk_desk:
        return {"error": "Risk Desk non initialise"}
    _risk_desk.session_config.clear_global_reduction()
    return {"ok": True}


def _get_param(name: str):
    """Helper pour recuperer un parametre par nom."""
    if not _risk_desk:
        return None
    sc = _risk_desk.session_config
    return {
        "position_size_mult": sc.position_size_mult,
        "atr_stop_mult": sc.atr_stop_mult,
        "max_trades": sc.max_trades,
        "cb_strictness": sc.cb_strictness,
    }.get(name)


# ==================================================================
# BROKER CONNECTION
# ==================================================================

_broker = None

def _get_broker():
    global _broker
    if not _broker:
        from core.broker_connector import BrokerConnector
        _broker = BrokerConnector()
    return _broker


@app.post("/api/broker/connect")
async def broker_connect(username: str = "", api_key: str = "",
                         instrument: str = "MNQ"):
    """Connecte le broker et demarre le flux de marche + suivi trades."""
    broker = _get_broker()
    result = await broker.connect(username, api_key)

    if result.get("ok"):
        await _start_broker_services(broker, instrument)
        result["feed"] = "started"
        result["instrument"] = instrument

    return result


@app.post("/api/broker/select-accounts")
async def broker_select_accounts(account_ids: str):
    """Selectionne les comptes a proteger. account_ids = '123,456,789'"""
    broker = _get_broker()
    ids = [int(x.strip()) for x in account_ids.split(",") if x.strip()]
    broker.select_accounts(ids)

    # Sync balance du broker → Risk Desk à la connexion
    if _risk_desk and ids:
        try:
            accounts = await broker.get_accounts()
            for acc in accounts:
                if acc["id"] == ids[0]:
                    _risk_desk.sync_from_broker(acc.get("balance", 0))
                    break
        except Exception as e:
            pass

    # Demarrer le trade monitor sur ces comptes
    await broker.start_trade_monitor(interval=2)
    return {"ok": True, "selected": ids}


async def _start_broker_services(broker, instrument: str = "MNQ"):
    """Demarre le market feed + trade monitor."""
    if _risk_desk:
        broker.set_callbacks(
            bar_callback=_risk_desk.feed_bar,
            price_callback=_risk_desk.feed_price,
            trade_callback=_risk_desk.record_trade,
            enforce_callback=_risk_desk.enforce_blocks,
            sync_callback=_risk_desk.sync_from_broker,
        )
    await broker.start_market_feed(instrument=instrument, interval=30)
    if broker.selected_account_ids:
        await broker.start_trade_monitor(interval=2)


@app.get("/api/broker/status")
async def broker_status():
    """Statut de la connexion broker."""
    broker = _get_broker()
    return broker.get_status()


@app.get("/api/broker/accounts")
async def broker_accounts():
    """Liste les comptes avec balance a jour."""
    broker = _get_broker()
    accounts = await broker.get_accounts()
    return {"accounts": accounts, "connected": broker.connected}


@app.get("/api/broker/positions")
async def broker_positions(account_id: int):
    """Positions ouvertes sur un compte."""
    broker = _get_broker()
    return await broker.get_positions(account_id)


@app.get("/api/broker/trades")
async def broker_trades(account_id: int):
    """Trades du jour — via les positions du broker."""
    broker = _get_broker()
    positions = await broker.get_positions(account_id)
    return {"account_id": account_id, "positions": positions}


# ==================================================================
# LICENSING & STRIPE
# ==================================================================

_license_mgr = None

def _get_license_mgr():
    global _license_mgr
    if not _license_mgr:
        from core.licensing import LicenseManager
        _license_mgr = LicenseManager()
    return _license_mgr


# ── Stripe Checkout ──

@app.post("/api/stripe/create-checkout")
async def stripe_create_checkout(email: str, firm: str, plan: str):
    """Cree une session Stripe Checkout pour $29/mois."""
    import stripe
    stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "")
    price_id = os.getenv("STRIPE_PRICE_ID", "")

    if not stripe.api_key or not price_id:
        return {"error": "Stripe non configure (STRIPE_SECRET_KEY et STRIPE_PRICE_ID dans .env)"}

    try:
        base_url = os.getenv("APP_URL", "http://localhost:8001")
        session = stripe.checkout.Session.create(
            mode="subscription",
            payment_method_types=["card"],
            customer_email=email,
            line_items=[{"price": price_id, "quantity": 1}],
            metadata={"firm": firm, "plan": plan, "email": email},
            success_url=f"{base_url}/risk-desk?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{base_url}/#pricing",
        )
        return {"checkout_url": session.url, "session_id": session.id}
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/stripe/webhook")
async def stripe_webhook(request: Request):
    """Webhook Stripe — active la licence apres paiement."""
    import stripe
    stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "")
    webhook_secret = os.getenv("STRIPE_WEBHOOK_SECRET", "")

    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")

    try:
        if not webhook_secret:
            logger.error("Stripe webhook: STRIPE_WEBHOOK_SECRET non configure, rejet")
            return {"error": "Webhook secret not configured"}
        event = stripe.Webhook.construct_event(payload, sig, webhook_secret)
    except Exception as e:
        logger.error(f"Stripe webhook error: {e}")
        return {"error": str(e)}

    if event.get("type") == "checkout.session.completed":
        session = event["data"]["object"]
        meta = session.get("metadata", {})
        email = meta.get("email", session.get("customer_email", ""))
        firm = meta.get("firm", "topstep")
        plan = meta.get("plan", "50k")

        mgr = _get_license_mgr()
        lic = mgr.create_paid(
            trader_id=email.split("@")[0],
            email=email,
            firm=firm,
            plan=plan,
            stripe_customer_id=session.get("customer", ""),
            stripe_subscription_id=session.get("subscription", ""),
        )
        logger.info(f"Stripe: licence creee pour {email} — {lic.license_key}")

    elif event.get("type") == "customer.subscription.deleted":
        sub = event["data"]["object"]
        sub_id = sub.get("id", "")
        mgr = _get_license_mgr()
        for lic in mgr._licenses.values():
            if lic.stripe_subscription_id == sub_id:
                mgr.deactivate(lic.license_key)
                logger.info(f"Stripe: licence desactivee {lic.license_key} (abo annule)")

    return {"ok": True}


# ── License validation ──

@app.post("/api/license/validate")
async def license_validate(key: str):
    """Valide une cle de licence."""
    mgr = _get_license_mgr()
    lic = mgr.validate_key(key)
    if not lic:
        return {"valid": False, "error": "Cle invalide ou expiree"}
    return {
        "valid": True,
        "type": lic.license_type,
        "firm": lic.firm,
        "plan": lic.plan,
        "days_remaining": lic.days_remaining(),
        "copy_accounts": lic.copy_account_ids,
    }


@app.post("/api/license/trial")
async def license_trial(trader_id: str, email: str = "",
                        firm: str = "topstep", plan: str = "50k"):
    """Cree une licence trial (7 jours)."""
    mgr = _get_license_mgr()
    # Verifier que l'email n'a pas deja un trial
    existing = mgr.get_licenses_for_email(email)
    trials = [l for l in existing if l.license_type == "trial"]
    if trials:
        return {"error": "Un trial existe deja pour cet email", "key": trials[0].license_key}
    lic = mgr.create_trial(trader_id, email, firm, plan)
    return {"key": lic.license_key, "expires": lic.expires_at, "days": 7}


@app.post("/api/license/activate-promo")
async def license_activate_promo(code: str, trader_id: str, email: str = "",
                                  firm: str = "topstep", plan: str = "50k"):
    """Active un code promo."""
    mgr = _get_license_mgr()
    lic = mgr.activate_promo(trader_id, code, email, firm, plan)
    if not lic:
        return {"error": "Code invalide, desactive ou epuise"}
    return {"key": lic.license_key, "expires": lic.expires_at, "days": lic.days_remaining()}


# ── Copy accounts ──

@app.post("/api/license/add-copy-account")
async def license_add_copy(key: str, account_id: str, auth=Depends(require_admin)):
    """Ajoute un compte copie a une licence."""
    mgr = _get_license_mgr()
    ok = mgr.add_copy_account(key, account_id)
    if not ok:
        return {"error": "Licence invalide"}
    return {"ok": True}


# ── Admin: promo codes ──

@app.post("/api/admin/promo/create")
async def admin_create_promo(duration_days: int = 30, max_uses: int = 1,
                              note: str = "", code: str = "",
                              auth=Depends(require_admin)):
    """Cree un code promo."""
    mgr = _get_license_mgr()
    promo = mgr.create_promo_code(duration_days, max_uses, note, code)
    return {"code": promo.code, "duration_days": promo.duration_days, "max_uses": promo.max_uses}


@app.post("/api/admin/promo/batch")
async def admin_batch_promo(count: int, duration_days: int = 30,
                             note: str = "", auth=Depends(require_admin)):
    """Cree un lot de codes promo (pour une classe d'eleves)."""
    mgr = _get_license_mgr()
    promos = mgr.create_batch_promo(count, duration_days, note)
    return {"codes": [p.code for p in promos], "count": len(promos)}


@app.get("/api/admin/promo/list")
async def admin_list_promos(auth=Depends(require_admin)):
    """Liste tous les codes promo."""
    mgr = _get_license_mgr()
    return mgr.list_promo_codes()


@app.get("/api/admin/licenses")
async def admin_list_licenses(auth=Depends(require_admin)):
    """Liste toutes les licences actives."""
    mgr = _get_license_mgr()
    return {
        "stats": mgr.get_stats(),
        "active": [
            {
                "key": l.license_key,
                "type": l.license_type,
                "email": l.email,
                "firm": l.firm,
                "plan": l.plan,
                "days_remaining": l.days_remaining(),
                "copy_accounts": l.copy_account_ids,
            }
            for l in mgr.get_all_active()
        ],
    }


# ==================================================================
# WEBSOCKET
# ==================================================================

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    _ws_clients.add(websocket)
    logger.info(f"WebSocket client connecte ({len(_ws_clients)} total)")

    try:
        while True:
            if _brain:
                try:
                    status = await _brain.get_status()
                    await websocket.send_json({"type": "status", "data": status})

                    if _brain.aggregator:
                        signal = _brain.aggregator.get_last_signal()
                        await websocket.send_json({"type": "signal", "data": signal})

                    if _brain.risk:
                        risk = _brain.risk.get_status()
                        await websocket.send_json({"type": "risk", "data": risk})

                    if _risk_desk:
                        fw = _risk_desk.get_framework()
                        await websocket.send_json({"type": "risk_desk", "data": fw.to_dict()})

                except WebSocketDisconnect:
                    break
                except Exception as e:
                    logger.debug(f"WebSocket send erreur: {e}")
                    break

            await asyncio.sleep(1)

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.debug(f"WebSocket erreur: {e}")
    finally:
        _ws_clients.discard(websocket)
        logger.info(f"WebSocket client deconnecte ({len(_ws_clients)} restants)")


async def broadcast(message: dict):
    """Envoie un message a tous les clients WebSocket"""
    dead = set()
    for ws in list(_ws_clients):
        try:
            await ws.send_json(message)
        except Exception:
            dead.add(ws)
    _ws_clients -= dead


async def start_server(brain=None):
    """Demarre le serveur FastAPI"""
    if brain:
        create_app(brain)

    port = int(os.getenv('TRADING_PORT', '8001'))
    logger.info(f"Dashboard trading: http://localhost:{port}")

    config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()
