"""
OptionsReader — Lit les niveaux options NQ depuis la base PostgreSQL de hubtrading.fr
Table: options_text_data — niveaux embarqués dans trading_view_levels (texte)
"""

import psycopg2
import os
import re
from dataclasses import dataclass, field
from typing import Optional
from loguru import logger
from datetime import date


@dataclass
class OptionsLevels:
    """Niveaux options NQ récupérés depuis hubtrading.fr DB."""
    date: date = None

    # Gamma levels
    gamma_wall_up: Optional[float] = None
    gamma_wall_down: Optional[float] = None
    hvl: Optional[float] = None  # High Vol Level / Vol Trigger

    # Call/Put walls
    call_wall: Optional[float] = None
    put_wall: Optional[float] = None

    # Zero gamma level
    zero_gamma: Optional[float] = None

    # Contexte
    market_bias: str = "neutral"
    gamma_condition: str = "neutral"
    pc_oi: Optional[float] = None
    qscore_option: Optional[int] = None

    raw_data: dict = field(default_factory=dict)

    def get_key_levels(self) -> list:
        """Retourne tous les niveaux triés pour affichage dashboard."""
        levels = []
        if self.call_wall:
            levels.append({'price': self.call_wall, 'label': 'Call Resistance', 'color': '#22c55e'})
        if self.gamma_wall_up:
            levels.append({'price': self.gamma_wall_up, 'label': 'Gamma Wall Up', 'color': '#22c55e'})
        if self.hvl:
            levels.append({'price': self.hvl, 'label': 'HVL', 'color': '#f59e0b'})
        if self.zero_gamma:
            levels.append({'price': self.zero_gamma, 'label': 'Zero Gamma', 'color': '#a78bfa'})
        if self.put_wall:
            levels.append({'price': self.put_wall, 'label': 'Put Support', 'color': '#ef4444'})
        if self.gamma_wall_down:
            levels.append({'price': self.gamma_wall_down, 'label': 'Gamma Wall Down', 'color': '#ef4444'})
        return sorted(levels, key=lambda x: x['price'], reverse=True)

    def get_bias_for_price(self, price: float) -> str:
        """Retourne le biais gamma selon la position du prix."""
        if self.hvl and price > self.hvl:
            return "POSITIVE_GAMMA"
        elif self.hvl and price < self.hvl:
            return "NEGATIVE_GAMMA"

        if self.gamma_wall_up and self.gamma_wall_down:
            mid = (self.gamma_wall_up + self.gamma_wall_down) / 2
            return "BULLISH_GAMMA" if price > mid else "BEARISH_GAMMA"

        return "NEUTRAL"


class OptionsReader:
    """Lit les niveaux options depuis PostgreSQL (table options_text_data)."""

    def __init__(self):
        self.db_url = os.getenv('HUBTRADING_DB_URL', '')
        if not self.db_url:
            logger.warning("HUBTRADING_DB_URL non configure dans .env -- options desactivees")

    def _connect(self):
        if not self.db_url:
            return None
        return psycopg2.connect(self.db_url)

    def _parse_trading_view_levels(self, text: str) -> dict:
        """
        Parse le champ trading_view_levels.
        Format: "$NQ1!: Call Resistance, 26000, Put Support, 25000, HVL, 25220, ..."
        Retourne un dict {label: value} pour chaque paire label/valeur trouvée.
        """
        if not text:
            return {}

        result = {}

        # Supprime le préfixe ticker (ex: "$NQ1!: " ou "NQ: ")
        text = re.sub(r'^[^:]*:\s*', '', text.strip())

        # Split par virgule et parse les paires label, value
        parts = [p.strip() for p in text.split(',')]

        i = 0
        while i < len(parts) - 1:
            label = parts[i].strip()
            value_str = parts[i + 1].strip()

            # Tente de parser comme nombre
            try:
                value = float(value_str.replace(' ', ''))
                result[label.lower()] = value
                i += 2
            except ValueError:
                # Pas un nombre, avance d'un
                i += 1

        return result

    def _map_levels_to_options(self, parsed: dict, levels: 'OptionsLevels'):
        """Mappe les niveaux parsés vers les champs OptionsLevels."""
        # Mapping des labels possibles vers les champs
        label_map = {
            'call_wall': ['call resistance', 'call wall', 'call_wall', 'call_resistance',
                          'major call', 'top call'],
            'put_wall': ['put support', 'put wall', 'put_wall', 'put_support',
                         'major put', 'top put'],
            'hvl': ['hvl', 'high vol level', 'vol trigger', 'volatility trigger',
                    'hv level', 'high volume level'],
            'gamma_wall_up': ['gamma wall up', 'gamma resistance', 'gamma_wall_up',
                              'upper gamma', 'gex wall up'],
            'gamma_wall_down': ['gamma wall down', 'gamma support', 'gamma_wall_down',
                                'lower gamma', 'gex wall down'],
            'zero_gamma': ['zero gamma', 'gamma flip', 'zero_gamma', 'gamma zero',
                           'zero gex', 'gex flip'],
        }

        for field_name, candidates in label_map.items():
            for candidate in candidates:
                if candidate in parsed:
                    setattr(levels, field_name, parsed[candidate])
                    break

    def get_today_levels(self, asset: str = 'NQ') -> Optional[OptionsLevels]:
        """Récupère les niveaux options du jour depuis la DB."""
        if not self.db_url:
            return None

        try:
            conn = self._connect()
            cur = conn.cursor()

            # Requête : dernier enregistrement pour l'asset NQ
            cur.execute("""
                SELECT * FROM options_text_data
                WHERE asset = %s
                ORDER BY scraped_at DESC
                LIMIT 1
            """, (asset,))

            row = cur.fetchone()
            if not row:
                # Fallback sans filtre asset
                cur.execute("""
                    SELECT * FROM options_text_data
                    ORDER BY scraped_at DESC
                    LIMIT 1
                """)
                row = cur.fetchone()

            if not row:
                logger.warning("Aucune donnee dans options_text_data")
                cur.close()
                conn.close()
                return None

            # Convertit en dict
            col_names = [desc[0] for desc in cur.description]
            data = dict(zip(col_names, row))

            cur.close()
            conn.close()

            # Crée OptionsLevels
            scraped_at = data.get('scraped_at')
            levels = OptionsLevels(
                date=scraped_at.date() if hasattr(scraped_at, 'date') else date.today(),
                raw_data={k: str(v) for k, v in data.items() if v is not None},
            )

            # Parse trading_view_levels
            tv_levels_text = data.get('trading_view_levels', '')
            parsed = self._parse_trading_view_levels(tv_levels_text)
            self._map_levels_to_options(parsed, levels)

            # Champs directs
            gamma_cond = data.get('gamma_condition', '')
            if gamma_cond:
                levels.gamma_condition = str(gamma_cond).strip()
                if 'positive' in gamma_cond.lower():
                    levels.market_bias = "positive_gamma"
                elif 'negative' in gamma_cond.lower():
                    levels.market_bias = "negative_gamma"
                else:
                    levels.market_bias = "neutral"

            pc_oi = data.get('pc_oi')
            if pc_oi is not None:
                try:
                    levels.pc_oi = float(pc_oi)
                except (TypeError, ValueError):
                    pass

            qscore = data.get('qscore_option')
            if qscore is not None:
                try:
                    levels.qscore_option = int(qscore)
                except (TypeError, ValueError):
                    pass

            n_levels = len(levels.get_key_levels())
            logger.info(
                f"Options {asset} : {n_levels} niveaux | "
                f"gamma={levels.gamma_condition} | pc_oi={levels.pc_oi}"
            )
            return levels

        except Exception as e:
            logger.error(f"Erreur lecture options DB : {e}")
            return None

    def discover_schema(self) -> dict:
        """Découvre le schéma de la DB (debug)."""
        if not self.db_url:
            return {'error': 'HUBTRADING_DB_URL non configure'}

        result = {'tables': {}}
        try:
            conn = self._connect()
            cur = conn.cursor()

            cur.execute("""
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = 'public' ORDER BY table_name
            """)
            tables = [row[0] for row in cur.fetchall()]

            for table in tables:
                cur.execute("""
                    SELECT column_name, data_type
                    FROM information_schema.columns
                    WHERE table_name = %s ORDER BY ordinal_position
                """, (table,))
                cols = cur.fetchall()

                sample = []
                try:
                    cur.execute(f'SELECT * FROM "{table}" ORDER BY 1 DESC LIMIT 2')
                    col_names = [desc[0] for desc in cur.description]
                    for row in cur.fetchall():
                        sample.append(dict(zip(col_names, [str(v) for v in row])))
                except Exception:
                    conn.rollback()

                result['tables'][table] = {
                    'columns': [{'name': c[0], 'type': c[1]} for c in cols],
                    'sample': sample,
                }

            cur.close()
            conn.close()

        except Exception as e:
            result['error'] = str(e)

        return result
