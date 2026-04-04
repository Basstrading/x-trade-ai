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

        # Regrouper les fills en round-trips (1 entree + sortie = 1 trade)
        trades = self._fills_to_round_trips(raw_fills)

        # Grouper par jour
        by_day = defaultdict(list)
        for t in trades:
            d = (t.get('entry_time') or '')[:10]
            if d:
                by_day[d].append(t)

        # Calculer les metriques
        metrics = self._compute_metrics(trades)
        daily_metrics = {d: self._compute_metrics(dt) for d, dt in by_day.items()}

        # Detecter les patterns comportementaux
        patterns = self._detect_patterns(trades, by_day, metrics, daily_metrics)

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
            "total_trades": len(trades),
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
            pnl = (t.get('pnl') or 0)
            fees = (t.get('fees') or 0)
            pnls.append(pnl - fees)

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
                    h_et = (dt.hour - 4) % 24
                    pnl = (t.get('pnl') or 0) - (t.get('fees') or 0)
                    by_hour[h_et].append(pnl)
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

        # 2. OVERTRADING — > 12 trades dans une journee
        overtrading_days = []
        for d, dm in daily_metrics.items():
            if dm['total_trades'] > 12:
                overtrading_days.append((d, dm['total_trades'], dm['net_pnl']))

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

    # ── Rapport texte ──

    def _format_report(self, metrics, daily_metrics, patterns, strengths,
                       weaknesses, action_plan, desk_rule, trades) -> str:
        n = len(trades)
        days = len(daily_metrics)

        report = []
        report.append("## DIAGNOSTIC COMPORTEMENTAL")
        report.append("")

        # Resume
        if metrics['net_pnl'] > 0:
            report.append(
                f"Sur {n} trades ({days} jours), tu es a +${metrics['net_pnl']:,.2f} "
                f"avec un win rate de {metrics['win_rate']}% et un profit factor de "
                f"{metrics['profit_factor']}. "
            )
        else:
            report.append(
                f"Sur {n} trades ({days} jours), tu es a ${metrics['net_pnl']:,.2f}. "
                f"Win rate: {metrics['win_rate']}%, profit factor: {metrics['profit_factor']}. "
            )

        if patterns:
            severity_count = sum(1 for p in patterns if p['severity'] == 'critical')
            report.append(
                f"J'ai detecte {len(patterns)} patterns, dont {severity_count} critiques. "
                f"C'est ca qui te coute de l'argent, pas le marche."
            )

        # Forces
        report.append("")
        report.append("### Tes forces")
        for s in strengths:
            report.append(f"- {s}")

        # Patterns
        if patterns:
            report.append("")
            report.append("### Tes patterns destructeurs")
            severity_icons = {"critical": "RED", "serious": "ORANGE", "moderate": "YELLOW"}
            for p in sorted(patterns, key=lambda x: {'critical': 0, 'serious': 1, 'moderate': 2}.get(x['severity'], 3)):
                icon = severity_icons.get(p['severity'], '')
                report.append(f"")
                report.append(f"**{p['name']}** [{icon}]")
                report.append(f"- Preuve: {p['evidence']}")
                report.append(f"- Impact: {p['impact']}")
                report.append(f"- Solution: {p['solution']}")

        # Plan
        report.append("")
        report.append("### Plan d'action (cette semaine)")
        for i, a in enumerate(action_plan, 1):
            report.append(f"{i}. {a}")

        # Desk rule
        report.append("")
        report.append(f"### A afficher sur ton ecran")
        report.append(f"> {desk_rule}")

        # Metriques detaillees
        report.append("")
        report.append("### Metriques detaillees")
        report.append(f"- Trades: {metrics['total_trades']} ({metrics['wins']}W / {metrics['losses']}L)")
        report.append(f"- Win rate: {metrics['win_rate']}%")
        report.append(f"- R:R moyen: {metrics['rr_ratio']}")
        report.append(f"- Profit factor: {metrics['profit_factor']}")
        report.append(f"- Esperance/trade: ${metrics['expectancy']:+,.2f}")
        report.append(f"- Plus gros gain: ${metrics['largest_win']:+,.2f}")
        report.append(f"- Plus grosse perte: ${metrics['largest_loss']:+,.2f}")
        report.append(f"- Drawdown max: ${metrics['max_drawdown']:+,.2f}")
        report.append(f"- Serie gagnante max: {metrics['max_consec_wins']}")
        report.append(f"- Serie perdante max: {metrics['max_consec_losses']}")

        if metrics['best_hour_et'] is not None:
            report.append(f"- Meilleure heure (ET): {metrics['best_hour_et']}h")
        if metrics['worst_hour_et'] is not None:
            report.append(f"- Pire heure (ET): {metrics['worst_hour_et']}h")

        if metrics['session_pnl']:
            report.append(f"- PnL par session: {metrics['session_pnl']}")

        return "\n".join(report)
