"""
Trading Coach — Analyse comportementale basée sur le skill trading-behavior-analyst.
40 ans d'expérience desk trader, analyse les patterns, donne des diagnostics actionnables.
"""
import math
from datetime import datetime, timedelta
from collections import defaultdict
from typing import List, Optional
from loguru import logger

from core.supabase_client import SupabaseClient


class TradingCoach:
    """Analyse les trades d'un user et produit des diagnostics comportementaux."""

    def __init__(self, supabase: SupabaseClient):
        self.sb = supabase

    async def analyze(self, user_id: str, days: int = 30,
                      broker_account_id: str = None) -> dict:
        """
        Analyse complete d'un user.
        Retourne un diagnostic structure.
        """
        raw_fills = await self.sb.get_trades(user_id)
        if not raw_fills:
            return {"error": "Aucun trade trouve", "trades_analyzed": 0}

        # Filtrer par broker_account si specifie
        if broker_account_id:
            raw_fills = [t for t in raw_fills if t.get('broker_account_id') == broker_account_id]

        # Compter les comptes actifs
        accounts_count = len(set(
            t.get('broker_account_id') for t in raw_fills
            if t.get('broker_account_id')
        ))

        # Separer les broker_account_ids pour identifier le compte principal
        ba_ids = list(set(
            t.get('broker_account_id') for t in raw_fills
            if t.get('broker_account_id')
        ))

        # Fills de sortie = ceux avec un PnL != 0
        exit_fills = [t for t in raw_fills if (t.get('pnl') or 0) != 0]

        # Pour les metriques PnL: tous les comptes cumules
        trades = exit_fills

        # Pour le comptage des trades: 1 seul compte (le principal)
        # Car les copies sont les memes trades repliques
        # Un trade de 3 contrats = 3 fills d'entree mais 1 seul trade
        # On regroupe les entrees < 5 sec d'ecart = meme trade
        if ba_ids:
            primary_ba = ba_ids[0]
            primary_fills = [t for t in raw_fills if t.get('broker_account_id') == primary_ba]
            primary_exits = [t for t in primary_fills if (t.get('pnl') or 0) != 0]
            primary_entries = sorted(
                [t for t in primary_fills if (t.get('pnl') or 0) == 0],
                key=lambda x: x.get('entry_time', '')
            )
            # Regrouper les entrees proches (< 5s = meme ordre)
            real_trade_count = 0
            last_entry_time = None
            for e in primary_entries:
                ts = e.get('entry_time', '')
                try:
                    et = datetime.fromisoformat(ts.replace('Z', '+00:00'))
                    if last_entry_time is None or (et - last_entry_time).total_seconds() > 5:
                        real_trade_count += 1
                    last_entry_time = et
                except Exception:
                    real_trade_count += 1
        else:
            real_trade_count = len(exit_fills)
            primary_exits = exit_fills

        # Grouper par jour (exits de tous les comptes pour le PnL)
        by_day = defaultdict(list)
        for t in exit_fills:
            d = (t.get('entry_time') or '')[:10]
            if d:
                by_day[d].append(t)

        # Grouper par jour pour le comptage (1 seul compte)
        by_day_primary = defaultdict(list)
        for t in primary_exits:
            d = (t.get('entry_time') or '')[:10]
            if d:
                by_day_primary[d].append(t)

        # Calculer les metriques (PnL = tous comptes, trades = 1 compte)
        metrics = self._compute_metrics(trades)
        metrics['real_trade_count'] = real_trade_count  # Vrais round-trips (1 compte)
        # Recalculer l'esperance par vrai trade (pas par fill)
        if real_trade_count > 0:
            metrics['expectancy'] = round(metrics['net_pnl'] / real_trade_count, 2)
        daily_metrics = {d: self._compute_metrics(dt) for d, dt in by_day.items()}

        # Ajouter le vrai nombre de trades par jour (1 compte, entrees regroupees)
        if ba_ids:
            for d in daily_metrics:
                day_entries = sorted(
                    [t for t in primary_entries if (t.get('entry_time') or '')[:10] == d],
                    key=lambda x: x.get('entry_time', '')
                )
                count = 0
                last_t = None
                for e in day_entries:
                    try:
                        et = datetime.fromisoformat(e['entry_time'].replace('Z', '+00:00'))
                        if last_t is None or (et - last_t).total_seconds() > 5:
                            count += 1
                        last_t = et
                    except Exception:
                        count += 1
                daily_metrics[d]['real_trade_count'] = count

        # Detecter les patterns (utiliser les fills du compte principal pour les patterns temporels)
        patterns = self._detect_patterns(primary_exits, by_day_primary, metrics, daily_metrics)

        # Identifier forces et faiblesses
        strengths = self._identify_strengths(metrics, daily_metrics, patterns)
        weaknesses = self._identify_weaknesses(metrics, daily_metrics, patterns)

        # Generer le plan d'action
        action_plan = self._generate_action_plan(patterns, weaknesses)

        # Regle de desk
        desk_rule = self._generate_desk_rule(patterns)

        # Generer le rapport complet en texte
        full_report = self._format_report(
            metrics, daily_metrics, patterns, strengths, weaknesses,
            action_plan, desk_rule, trades
        )

        # Niveau de confiance
        n = len(trades)
        if n >= 100:
            confidence = "high"
        elif n >= 30:
            confidence = "medium"
        else:
            confidence = "low"

        result = {
            "report_type": "on_demand",
            "period_start": min(by_day.keys()) if by_day else None,
            "period_end": max(by_day.keys()) if by_day else None,
            "total_trades": real_trade_count,
            "win_rate": metrics["win_rate"],
            "profit_factor": metrics["profit_factor"],
            "net_pnl": metrics["net_pnl"],
            "patterns_detected": patterns,
            "strengths": strengths,
            "weaknesses": weaknesses,
            "action_plan": action_plan,
            "desk_rule": desk_rule,
            "full_report": full_report,
            "trades_analyzed": len(trades),
            "confidence_level": confidence,
            "accounts_count": accounts_count,
            "metrics": metrics,
            "daily_metrics": daily_metrics,
        }

        # Sauvegarder le rapport
        report_to_save = {k: v for k, v in result.items()
                          if k not in ('metrics', 'daily_metrics')}
        await self.sb.save_coaching_report(user_id, report_to_save)

        return result

    # ── Fills → Round-trips ──

    def _fills_to_round_trips(self, fills: List[dict]) -> List[dict]:
        """
        Regroupe les fills bruts en round-trips reels.
        1 entree (pnl=null) + sortie(s) (pnl!=null) = 1 trade.
        Un trade de 3 contrats = 1 trade, pas 3.
        """
        sorted_fills = sorted(fills, key=lambda x: x.get('entry_time') or '')

        # Grouper par jour
        by_day = defaultdict(list)
        for f in sorted_fills:
            d = (f.get('entry_time') or '')[:10]
            by_day[d].append(f)

        trips = []
        for d, day_fills in by_day.items():
            pending = None
            entry_size = 0
            partial_pnl = 0.0
            partial_fees = 0.0
            exit_count = 0

            for f in day_fills:
                pnl = f.get('pnl') or 0
                fees = f.get('fees') or 0
                size = f.get('size') or 1
                is_entry = (pnl == 0 and fees <= size * 0.50)  # Entries have minimal/no PnL

                if is_entry:
                    # Sauver le trade precedent si en cours
                    if pending and exit_count > 0:
                        trips.append(self._make_trip(pending, partial_pnl, partial_fees, entry_size))
                    pending = f
                    entry_size = size
                    partial_pnl = 0.0
                    partial_fees = fees
                    exit_count = 0
                else:
                    partial_pnl += pnl
                    partial_fees += fees
                    exit_count += size
                    if pending and exit_count >= entry_size:
                        trips.append(self._make_trip(pending, partial_pnl, partial_fees, entry_size))
                        pending = None
                        partial_pnl = partial_fees = 0.0
                        exit_count = entry_size = 0

            # Dernier trade
            if pending and exit_count > 0:
                trips.append(self._make_trip(pending, partial_pnl, partial_fees, entry_size))

        return trips

    def _make_trip(self, entry_fill: dict, pnl: float, fees: float, size: int) -> dict:
        """Construit un round-trip depuis un fill d'entree et le PnL accumule."""
        return {
            'entry_time': entry_fill.get('entry_time', ''),
            'direction': entry_fill.get('direction', ''),
            'entry_price': entry_fill.get('entry_price', 0),
            'instrument': entry_fill.get('instrument', ''),
            'session': entry_fill.get('session', ''),
            'size': size,
            'pnl': round(pnl, 2),
            'fees': round(fees, 2),
        }

    # ── Metriques ──

    def _compute_metrics(self, trades: List[dict]) -> dict:
        """Calcule toutes les metriques depuis une liste de trades."""
        if not trades:
            return self._empty_metrics()

        pnls = []
        for t in trades:
            # Utiliser net_pnl (colonne Supabase) si dispo, sinon pnl - fees
            net = t.get('net_pnl')
            if net is not None:
                pnls.append(net)
            else:
                pnls.append((t.get('pnl') or 0) - (t.get('fees') or 0))

        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        total = len(pnls)

        win_rate = len(wins) / total * 100 if total > 0 else 0
        avg_win = sum(wins) / len(wins) if wins else 0
        avg_loss = sum(losses) / len(losses) if losses else 0
        gross_profit = sum(wins)
        gross_loss = abs(sum(losses))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0
        expectancy = sum(pnls) / total if total > 0 else 0

        # Peak et drawdown
        running = peak = max_dd = 0.0
        for p in pnls:
            running += p
            if running > peak:
                peak = running
            dd = running - peak
            if dd < max_dd:
                max_dd = dd

        # Consecutive
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

        # Analyse temporelle
        by_hour = defaultdict(list)
        for t in trades:
            ts = t.get('entry_time', '')
            if len(ts) >= 13:
                try:
                    dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
                    # Convertir en heure Paris (UTC+1 CET / UTC+2 CEST)
                    try:
                        from zoneinfo import ZoneInfo
                        h_paris = dt.astimezone(ZoneInfo("Europe/Paris")).hour
                    except Exception:
                        h_paris = (dt.hour + 2) % 24  # Fallback CEST
                    net = t.get('net_pnl') if t.get('net_pnl') is not None else ((t.get('pnl') or 0) - (t.get('fees') or 0))
                    by_hour[h_paris].append(net)
                except Exception:
                    pass

        best_hour = worst_hour = None
        if by_hour:
            hour_pnl = {h: sum(ps) for h, ps in by_hour.items()}
            best_hour = max(hour_pnl, key=hour_pnl.get)
            worst_hour = min(hour_pnl, key=hour_pnl.get)

        # Analyse par session
        by_session = defaultdict(list)
        for t in trades:
            s = t.get('session', 'unknown')
            pnl = (t.get('pnl') or 0) - (t.get('fees') or 0)
            by_session[s].append(pnl)

        session_pnl = {s: round(sum(ps), 2) for s, ps in by_session.items()}

        return {
            "total_trades": total,
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(win_rate, 1),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "rr_ratio": round(abs(avg_win / avg_loss), 2) if avg_loss != 0 else 0,
            "profit_factor": round(profit_factor, 2),
            "expectancy": round(expectancy, 2),
            "net_pnl": round(sum(pnls), 2),
            "gross_profit": round(gross_profit, 2),
            "gross_loss": round(gross_loss, 2),
            "largest_win": round(max(pnls), 2) if pnls else 0,
            "largest_loss": round(min(pnls), 2) if pnls else 0,
            "peak_pnl": round(peak, 2),
            "max_drawdown": round(max_dd, 2),
            "max_consec_wins": max_cw,
            "max_consec_losses": max_cl,
            "best_hour_et": best_hour,
            "worst_hour_et": worst_hour,
            "session_pnl": session_pnl,
            "by_hour": {h: round(sum(ps), 2) for h, ps in by_hour.items()},
        }

    def _empty_metrics(self):
        return {
            "total_trades": 0, "wins": 0, "losses": 0, "win_rate": 0,
            "avg_win": 0, "avg_loss": 0, "rr_ratio": 0, "profit_factor": 0,
            "expectancy": 0, "net_pnl": 0, "gross_profit": 0, "gross_loss": 0,
            "largest_win": 0, "largest_loss": 0, "peak_pnl": 0, "max_drawdown": 0,
            "max_consec_wins": 0, "max_consec_losses": 0,
            "best_hour_et": None, "worst_hour_et": None,
            "session_pnl": {}, "by_hour": {},
        }

    # ── Detection de patterns ──

    def _detect_patterns(self, trades, by_day, metrics, daily_metrics) -> list:
        """Detecte les patterns comportementaux destructeurs."""
        patterns = []

        # 1. REVENGE TRADING — trade < 2min apres un perdant
        revenge_count = 0
        for d, day_trades in by_day.items():
            sorted_t = sorted(day_trades, key=lambda x: x.get('entry_time', ''))
            for i in range(1, len(sorted_t)):
                prev_pnl = (sorted_t[i-1].get('pnl') or 0)
                if prev_pnl < 0:
                    try:
                        t1 = datetime.fromisoformat(sorted_t[i-1]['entry_time'].replace('Z', '+00:00'))
                        t2 = datetime.fromisoformat(sorted_t[i]['entry_time'].replace('Z', '+00:00'))
                        if (t2 - t1).total_seconds() < 120:
                            revenge_count += 1
                    except Exception:
                        pass

        if revenge_count >= 3:
            pct = revenge_count / max(len(trades), 1) * 100
            patterns.append({
                "name": "Revenge Trading",
                "severity": "critical",
                "count": revenge_count,
                "evidence": f"{revenge_count} trades pris < 2min apres une perte ({pct:.0f}% des trades)",
                "impact": "Decisions emotionnelles, pas rationnelles. Chaque revenge trade a une esperance negative.",
                "solution": "Regle de desk : 5 minutes MINIMUM entre deux trades. Timer obligatoire.",
            })

        # 2. OVERTRADING — > 12 trades dans une journee (vrais round-trips)
        overtrading_days = []
        for d, dm in daily_metrics.items():
            rt = dm.get('real_trade_count', dm['total_trades'])
            if rt > 12:
                overtrading_days.append((d, rt, dm['net_pnl']))

        if overtrading_days:
            total_lost = sum(pnl for _, _, pnl in overtrading_days if pnl < 0)
            patterns.append({
                "name": "Overtrading",
                "severity": "critical",
                "count": len(overtrading_days),
                "evidence": f"{len(overtrading_days)} jours avec > 12 trades. "
                            f"Jours concernes: {', '.join(d for d, _, _ in overtrading_days)}",
                "impact": f"Perte totale sur ces jours: ${total_lost:,.0f}",
                "solution": "Cap strict a 12 trades/jour. Apres 12, STOP. Pas de negociation.",
            })

        # 3. SIZING INCONSISTANT — taille qui varie trop
        sizes = [t.get('size', 1) for t in trades]
        if len(set(sizes)) > 1:
            avg_size = sum(sizes) / len(sizes)
            variance = sum((s - avg_size) ** 2 for s in sizes) / len(sizes)
            std = math.sqrt(variance)
            cv = std / avg_size if avg_size > 0 else 0
            if cv > 0.5:
                patterns.append({
                    "name": "Sizing Inconsistant",
                    "severity": "serious",
                    "count": None,
                    "evidence": f"Tailles de position varient entre {min(sizes)} et {max(sizes)} "
                                f"(CV={cv:.1f}). Pas de logique de sizing constante.",
                    "impact": "Les gros trades perdants pesent disproportionnement.",
                    "solution": "Meme taille. Toujours. Le sizing se decide AVANT la session, pas pendant.",
                })

        # 4. TILT APRES PERTES CONSECUTIVES
        for d, day_trades in by_day.items():
            sorted_t = sorted(day_trades, key=lambda x: x.get('entry_time', ''))
            consec_loss = 0
            post_tilt_trades = 0
            post_tilt_pnl = 0
            in_tilt = False
            for t in sorted_t:
                pnl = (t.get('pnl') or 0)
                if pnl < 0:
                    consec_loss += 1
                else:
                    consec_loss = 0
                if consec_loss >= 3:
                    in_tilt = True
                if in_tilt:
                    post_tilt_trades += 1
                    post_tilt_pnl += pnl - (t.get('fees') or 0)

            if post_tilt_trades >= 5 and post_tilt_pnl < -100:
                patterns.append({
                    "name": "Tilt apres series perdantes",
                    "severity": "critical",
                    "count": post_tilt_trades,
                    "evidence": f"Le {d}: {post_tilt_trades} trades apres 3 pertes consecutives, "
                                f"PnL post-tilt: ${post_tilt_pnl:+,.0f}",
                    "impact": f"${abs(post_tilt_pnl):,.0f} perdus en mode tilt.",
                    "solution": "Apres 3 pertes consecutives: STOP. Pas de 4eme trade. Walk away.",
                })

        # 5. PERFORMANCE QUI S'EFFONDRE APRES-MIDI
        morning_pnl = afternoon_pnl = 0
        morning_trades = afternoon_trades = 0
        for t in trades:
            ts = t.get('entry_time', '')
            try:
                dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
                h_et = (dt.hour - 4) % 24
                pnl = (t.get('pnl') or 0) - (t.get('fees') or 0)
                if 9 <= h_et < 12:
                    morning_pnl += pnl
                    morning_trades += 1
                elif 12 <= h_et < 16:
                    afternoon_pnl += pnl
                    afternoon_trades += 1
            except Exception:
                pass

        if morning_trades >= 10 and afternoon_trades >= 10:
            if morning_pnl > 0 and afternoon_pnl < -abs(morning_pnl) * 0.5:
                patterns.append({
                    "name": "Fatigue cognitive apres-midi",
                    "severity": "moderate",
                    "count": None,
                    "evidence": f"Matin: ${morning_pnl:+,.0f} ({morning_trades} trades) | "
                                f"Apres-midi: ${afternoon_pnl:+,.0f} ({afternoon_trades} trades)",
                    "impact": f"L'apres-midi detruit ${abs(afternoon_pnl):,.0f} des gains du matin.",
                    "solution": "Trade uniquement le matin (9h-12h ET). L'edge meurt apres.",
                })

        # 6. BIAIS DIRECTIONNEL
        longs = sum(1 for t in trades if t.get('direction') == 'long')
        shorts = sum(1 for t in trades if t.get('direction') == 'short')
        total = longs + shorts
        if total >= 20:
            ratio = max(longs, shorts) / total
            if ratio > 0.80:
                dominant = "LONG" if longs > shorts else "SHORT"
                patterns.append({
                    "name": "Biais directionnel",
                    "severity": "moderate",
                    "count": None,
                    "evidence": f"{ratio*100:.0f}% des trades sont {dominant} ({longs}L / {shorts}S)",
                    "impact": "Tu rates 50% des opportunites. Le marche ne va pas que dans un sens.",
                    "solution": f"Force-toi a prendre au moins 30% de trades {'SHORT' if dominant == 'LONG' else 'LONG'}.",
                })

        return patterns

    # ── Forces & Faiblesses ──

    def _identify_strengths(self, metrics, daily_metrics, patterns) -> list:
        strengths = []

        if metrics['win_rate'] >= 50:
            strengths.append(f"Win rate solide a {metrics['win_rate']}% — tu sais lire le marche.")

        if metrics['profit_factor'] >= 1.5:
            strengths.append(
                f"Profit factor de {metrics['profit_factor']} — tes gains sont nettement "
                f"plus gros que tes pertes."
            )

        if metrics['max_consec_wins'] >= 5:
            strengths.append(
                f"Serie gagnante max de {metrics['max_consec_wins']} — "
                f"quand tu es en zone, tu capitalises."
            )

        # Jours gagnants
        winning_days = sum(1 for dm in daily_metrics.values() if dm['net_pnl'] > 0)
        total_days = len(daily_metrics)
        if total_days > 0 and winning_days / total_days >= 0.5:
            strengths.append(
                f"{winning_days}/{total_days} jours gagnants — consistance au rendez-vous."
            )

        if not strengths:
            strengths.append("Tu continues a trader malgre les difficultes — la perseverance est le prerequis #1.")

        return strengths

    def _identify_weaknesses(self, metrics, daily_metrics, patterns) -> list:
        weaknesses = []

        if metrics['max_drawdown'] < -500:
            weaknesses.append(
                f"Drawdown max de ${metrics['max_drawdown']:,.0f} — tu laisses les pertes courir trop loin."
            )

        if metrics['rr_ratio'] < 1.0 and metrics['rr_ratio'] > 0:
            weaknesses.append(
                f"R:R moyen de {metrics['rr_ratio']} — tu risques plus que ce que tu gagnes par trade."
            )

        if metrics['max_consec_losses'] >= 5:
            weaknesses.append(
                f"{metrics['max_consec_losses']} pertes consecutives — pas de circuit breaker?"
            )

        for p in patterns:
            if p['severity'] == 'critical':
                weaknesses.append(f"{p['name']}: {p['evidence']}")

        return weaknesses

    # ── Plan d'action ──

    def _generate_action_plan(self, patterns, weaknesses) -> list:
        plan = []
        seen = set()

        for p in sorted(patterns, key=lambda x: {'critical': 0, 'serious': 1, 'moderate': 2}.get(x['severity'], 3)):
            if len(plan) >= 3:
                break
            if p['name'] not in seen:
                plan.append(p['solution'])
                seen.add(p['name'])

        if not plan:
            plan.append("Continue comme ca. Tes metriques sont saines. Focus sur la consistance.")

        return plan

    def _generate_desk_rule(self, patterns) -> str:
        if any(p['name'] == 'Revenge Trading' for p in patterns):
            return "REGLE: 5 minutes entre chaque trade. Pas de raccourci. Timer."
        if any(p['name'] == 'Overtrading' for p in patterns):
            return "REGLE: 12 trades MAX par jour. Apres 12, ecran eteint."
        if any(p['name'] == 'Tilt apres series perdantes' for p in patterns):
            return "REGLE: 3 pertes = STOP. Walk away. Reviens demain."
        if any(p['name'] == 'Fatigue cognitive apres-midi' for p in patterns):
            return "REGLE: Pas de trade apres 12h ET. Ton edge est le matin."
        return "REGLE: Suis ton plan. Si tu doutes, ne trade pas. Le marche sera la demain."

    # ── Rapport texte complet (style desk trader) ──

    def _format_report(self, metrics, daily_metrics, patterns, strengths,
                       weaknesses, action_plan, desk_rule, trades) -> str:
        n = len(trades)
        days = len(daily_metrics)
        m = metrics
        r = []

        # ═══ INTRO ═══
        r.append("## DIAGNOSTIC COMPORTEMENTAL")
        r.append("")

        # Verdict en 1 phrase
        if m['net_pnl'] > 0 and m['profit_factor'] >= 1.5:
            verdict = f"Tu es rentable (+${m['net_pnl']:,.0f}) avec un edge reel. Maintenant il faut le proteger."
        elif m['net_pnl'] > 0:
            verdict = (
                f"Tu es positif (+${m['net_pnl']:,.0f}) mais sur un fil. "
                f"Ton profit factor de {m['profit_factor']} est fragile — "
                f"un mauvais jour efface tout."
            )
        else:
            verdict = (
                f"Tu es a ${m['net_pnl']:,.0f}. "
                f"Le probleme n'est pas ton analyse, c'est ton comportement. Les donnees le montrent."
            )
        r.append(f"**{verdict}**")
        r.append("")

        # ═══ CE QUI MARCHE ═══
        r.append("### Ce que tu fais bien")
        r.append("")

        # Analyse detaillee des forces
        winning_days = {d: dm for d, dm in daily_metrics.items() if dm['net_pnl'] > 0}
        losing_days = {d: dm for d, dm in daily_metrics.items() if dm['net_pnl'] < 0}

        if winning_days:
            best_day = max(winning_days.items(), key=lambda x: x[1]['net_pnl'])
            avg_win_day = sum(dm['net_pnl'] for dm in winning_days.values()) / len(winning_days)
            r.append(
                f"**Tes jours gagnants sont solides.** Sur {len(winning_days)} jours verts, "
                f"tu gagnes en moyenne ${avg_win_day:,.0f}/jour. "
                f"Ta meilleure journee ({best_day[0]}): +${best_day[1]['net_pnl']:,.0f} "
                f"en seulement {best_day[1]['total_trades']} trades. "
                f"Ca montre que quand tu es discipline, tu sais generer du profit."
            )
            r.append("")

        if m['rr_ratio'] >= 1.0:
            r.append(
                f"**Ton R:R est positif ({m['rr_ratio']}).** "
                f"Gain moyen: ${m['avg_win']:,.0f} vs perte moyenne: ${abs(m['avg_loss']):,.0f}. "
                f"Quand tu gagnes, tu gagnes plus que quand tu perds. "
                f"C'est la base d'un edge — beaucoup de traders n'ont pas ca."
            )
            r.append("")

        best_h = m.get('best_hour_et')
        if best_h is not None:
            paris_h = (best_h + 6) % 24
            r.append(
                f"**Tu as un creneau de performance clair: {paris_h}h (heure Paris).** "
                f"C'est ta golden hour. Les donnees montrent que tes meilleurs resultats "
                f"viennent de ce creneau. Un sniper ne tire pas 50 fois — il attend LE tir."
            )
            r.append("")

        for s in strengths:
            if s not in '\n'.join(r):
                r.append(f"- {s}")
        r.append("")

        # ═══ ANALYSE JOUR PAR JOUR ═══
        r.append("### Analyse jour par jour")
        r.append("")

        sorted_days = sorted(daily_metrics.keys())
        for d in sorted_days:
            dm = daily_metrics[d]
            net = dm['net_pnl']
            trades_count = dm['total_trades']
            wr = dm.get('win_rate', 0)
            dd = dm.get('max_drawdown', 0)
            consec = dm.get('max_consec_losses', 0)

            status = "VERT" if net > 0 else "ROUGE"
            r.append(f"**{d}** — {status}")

            if net > 0:
                if trades_count <= 12:
                    r.append(
                        f"  +${net:,.0f} en {trades_count} trades (WR {wr:.0f}%). "
                        f"Bonne discipline, nombre de trades raisonnable. "
                        f"C'est CA le trading rentable — pas de l'hyperactivite."
                    )
                else:
                    r.append(
                        f"  +${net:,.0f} mais {trades_count} trades — trop. "
                        f"Tu aurais probablement gagne plus en t'arretant plus tot. "
                        f"Le surtrading grignote tes gains meme les jours verts."
                    )
            else:
                if consec >= 3:
                    r.append(
                        f"  ${net:,.0f} avec {consec} pertes consecutives. "
                        f"Drawdown max: ${dd:,.0f}. "
                        f"Apres la 3eme perte, tu aurais du couper. "
                        f"Les trades suivants etaient du revenge trading — "
                        f"tu ne tradais plus le marche, tu tradais tes emotions."
                    )
                elif trades_count > 20:
                    r.append(
                        f"  ${net:,.0f} en {trades_count} trades. "
                        f"C'est de l'overtrading pur. A chaque trade tu paies des frais, "
                        f"tu accumules de la fatigue, et tu prends des decisions de moins en moins bonnes."
                    )
                else:
                    r.append(
                        f"  ${net:,.0f} en {trades_count} trades (WR {wr:.0f}%). "
                        f"Journee difficile mais le nombre de trades est correct."
                    )
            r.append("")

        # ═══ LES PATTERNS QUI TE COUTENT DE L'ARGENT ═══
        if patterns:
            r.append("### Les patterns qui te coutent de l'argent")
            r.append("")

            total_cost = 0
            for p in sorted(patterns, key=lambda x: {'critical': 0, 'serious': 1, 'moderate': 2}.get(x['severity'], 3)):
                sev = {"critical": "CRITIQUE", "serious": "SERIEUX", "moderate": "MODERE"}.get(p['severity'], '')
                r.append(f"**{p['name']}** [{sev}]")
                r.append("")
                r.append(f"*Ce que je vois :* {p['evidence']}")
                r.append("")

                # Explication comportementale
                if p['name'] == 'Revenge Trading':
                    r.append(
                        f"*Pourquoi c'est un probleme :* Le revenge trading, c'est quand ton cerveau "
                        f"reptilien prend le controle. Tu viens de perdre, tu veux te refaire "
                        f"immediatement. Sauf que dans cet etat, tu ne lis plus le marche — "
                        f"tu reagis a ta douleur. Les 28 revenge trades que je detecte ont "
                        f"probablement une esperance negative. Un trader de desk qui fait ca "
                        f"se fait virer. Pas un warning — virer. Parce que ca detruit des comptes."
                    )
                elif p['name'] == 'Overtrading':
                    r.append(
                        f"*Pourquoi c'est un probleme :* Le marche ne donne pas 20+ opportunites "
                        f"par jour sur un seul instrument. Si tu prends 20 trades, 5 sont "
                        f"probablement bons, et les 15 autres sont du bruit que tu trades par "
                        f"ennui, par impatience ou par addiction. Chaque trade en trop te coute "
                        f"des frais + du slippage + de l'energie mentale. L'overtrading est "
                        f"l'ennemi #1 des prop firm traders."
                    )
                elif 'Tilt' in p['name']:
                    r.append(
                        f"*Pourquoi c'est un probleme :* Apres 3 pertes d'affilee, ton cortisol "
                        f"est au plafond. Ton jugement est altere — exactement comme un joueur "
                        f"de poker en tilt. Les trades que tu prends dans cet etat ne sont pas "
                        f"bases sur ton edge mais sur ton besoin de te refaire. La data montre "
                        f"que ${abs(float(p.get('count', 0)))} trades post-tilt t'ont coute cher."
                    )
                else:
                    r.append(f"*Impact :* {p['impact']}")
                r.append("")

                r.append(f"*Ce que tu dois faire :* {p['solution']}")
                r.append("")

        # ═══ OBJECTIF PAYOUT ═══
        r.append("### Ta route vers le payout")
        r.append("")

        payout_target = 3000  # Topstep 50K
        remaining = payout_target - m['net_pnl']
        days_needed = 0
        if remaining > 0:
            avg_day_pnl = m['net_pnl'] / days if days > 0 else 0
            if avg_day_pnl > 0:
                days_needed = remaining / avg_day_pnl
                r.append(
                    f"Il te reste ${remaining:,.0f} pour atteindre le payout de ${payout_target:,.0f}. "
                    f"A ton rythme actuel (+${avg_day_pnl:,.0f}/jour en moyenne), "
                    f"ca represente environ {days_needed:.0f} jours de trading."
                )
            else:
                r.append(
                    f"Il te reste ${remaining:,.0f} pour le payout. "
                    f"Ta moyenne journaliere est negative — il faut d'abord "
                    f"corriger les patterns avant de viser le payout."
                )
            r.append("")

            # Projection sans les patterns
            cost_patterns = sum(
                abs(float(str(p.get('impact', '0')).replace('$', '').replace(',', '').split()[0]))
                for p in patterns
                if p.get('impact', '').startswith('$')
            ) if patterns else 0

            if cost_patterns > 0:
                corrected_pnl = m['net_pnl'] + cost_patterns
                corrected_daily = corrected_pnl / days if days > 0 else 0
                if corrected_daily > 0:
                    faster_days = (payout_target - corrected_pnl) / corrected_daily
                    r.append(
                        f"**Si tu elimines tes patterns destructeurs**, ton PnL serait "
                        f"d'environ +${corrected_pnl:,.0f} au lieu de +${m['net_pnl']:,.0f}. "
                        f"Le payout arriverait en {faster_days:.0f} jours au lieu de {days_needed:.0f}. "
                        f"Les patterns te coutent du temps ET de l'argent."
                    )
                    r.append("")
        else:
            r.append(f"Tu as atteint l'objectif de payout! Focus sur la consistance maintenant.")
            r.append("")

        # ═══ BENCHMARKS ═══
        r.append("### Ou tu te situes vs un trader pro")
        r.append("")
        r.append("| Metrique | Toi | Debutant | Intermediaire | Pro |")
        r.append("|----------|-----|----------|---------------|-----|")
        r.append(f"| Win rate | {m['win_rate']}% | 30-40% | 45-55% | 50-60% |")
        r.append(f"| Profit factor | {m['profit_factor']} | 0.5-0.9 | 1.0-1.5 | 1.5-3.0 |")
        r.append(f"| R:R moyen | {m['rr_ratio']} | 0.5-0.8 | 1.0-1.5 | 1.5-3.0 |")
        r.append(f"| Max DD | ${m['max_drawdown']:,.0f} | -50% compte | -20% | -5-10% |")
        r.append(f"| Trades/jour | {n/days:.0f} | 15-30 | 5-12 | 2-6 |")
        r.append("")

        level = "debutant"
        if m['profit_factor'] >= 1.5 and m['win_rate'] >= 50:
            level = "intermediaire-pro"
        elif m['profit_factor'] >= 1.0:
            level = "intermediaire"

        r.append(
            f"**Ton profil actuel: {level}.** "
            f"Ton R:R de {m['rr_ratio']} est {'bon' if m['rr_ratio'] >= 1.0 else 'a ameliorer'}. "
            f"Ton nombre de trades/jour ({n/days:.0f}) est trop eleve pour un prop trader — "
            f"les pros font 2-6 trades/jour. Moins de trades = moins d'erreurs = plus de profit."
        )
        r.append("")

        # ═══ PLAN D'ACTION ═══
        r.append("### Plan d'action concret (cette semaine)")
        r.append("")
        for i, a in enumerate(action_plan, 1):
            r.append(f"**{i}.** {a}")
            r.append("")

        # ═══ DESK RULE ═══
        r.append("### Regle a afficher sur ton ecran")
        r.append("")
        r.append(f"> {desk_rule}")

        return "\n".join(r)
