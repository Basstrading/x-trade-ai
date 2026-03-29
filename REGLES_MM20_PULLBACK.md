# STRATEGIE MM20 PULLBACK — REGLES COMPLETES

---

## 1. NASDAQ (MNQ / NQ)

### Instrument
- **4 Micro NQ (MNQ)** = 8 USD/pt
- Ou 1 Mini NQ (NQ) = 20 USD/pt

### Horaires (heure de Paris)
- **Debut des trades** : 16h00 (ou 15h00 pendant le gap DST*)
- **Sortie forcee (FLAT)** : 20h39 (ou 19h39 pendant le gap DST*)
- *Gap DST = entre le 2e dimanche de mars US et le dernier dimanche de mars EU

### Conditions d'entree (TOUTES obligatoires)

| # | Condition | Detail |
|---|-----------|--------|
| 1 | **Alignement M5** | Cloture bougie 5min > SMA20 M5 (LONG) ou < SMA20 M5 (SHORT) |
| 2 | **Alignement H1** | Cloture bougie 1H > SMA20 H1 (LONG) ou < SMA20 H1 (SHORT) |
| 3 | **Distance H1 >= 75 pts** | abs(cloture H1 - SMA20 H1) >= 75 points — **FILTRE CLE** |
| 4 | **Pullback recent** | Dans les 10 dernieres bougies 5min, le prix a touche la SMA20 M5 (a 15 pts pres) |

### LONG : quand le prix est AU-DESSUS de la SMA20
- Cloture M5 > SMA20 M5
- Cloture H1 > SMA20 H1 (distance >= 75 pts)
- Le low d'une bougie a touche la SMA20 M5 dans les 10 derniers bars (pullback)
- → Achat a la cloture de la bougie 5min

### SHORT : quand le prix est EN-DESSOUS de la SMA20
- Cloture M5 < SMA20 M5
- Cloture H1 < SMA20 H1 (distance >= 75 pts)
- Le high d'une bougie a touche la SMA20 M5 dans les 10 derniers bars (pullback)
- → Vente a la cloture de la bougie 5min

### Gestion de position

| Parametre | Valeur |
|-----------|--------|
| **Take Profit** | +300 pts depuis l'entree |
| **Stop Loss max** | -200 pts depuis l'entree (protection crash) |
| **Trailing Stop** | Plus bas des 20 dernieres bougies 5min (LONG) / Plus haut (SHORT) |
| **Sortie temps** | 20h39 Paris (19h39 pendant gap DST) |

### Risk Management

| Parametre | Valeur |
|-----------|--------|
| Max trades / jour | 4 |
| Stop apres X pertes consecutives | 3 (arret pour la journee) |
| Perte max journaliere | -1,000 USD |

### Parametres moteur
```python
MM20BacktestEngine(
    tp_points=300,
    trail_bars=20,
    max_sl_pts=200,
    max_trades_day=4,
    sma_period=20,
    start_offset_min=30,    # 30min apres open US
    abs_start_hour=0,       # 0 = utilise start_offset dynamique
    daily_loss_stop=3,
    point_value=8.0,        # 4 MNQ
    daily_loss_usd=1000,
    pullback_bars=10,
    pullback_dist=15,
    min_h1_sma_dist=75,
)
```

### Resultats backtest (5 ans, mars 2021 - mars 2026)
- **1,330 trades** | WR 67.4% | **PF 3.72** | Sharpe 7.24
- **100% mois verts** (61/61)
- PnL total : +$464,478 (4 MNQ)
- Max Drawdown : $3,824

### Resultats 12 derniers mois (mars 2025 - mars 2026)
- **328 trades** | WR 64.6% | PF 3.35
- **13/13 mois positifs**
- PnL total : +$140,246 (4 MNQ)
- Max Drawdown : $3,824
- Pire mois : +$2,372 (Jul 2025) — quand meme vert
- Meilleur mois : +$28,964 (Avr 2025)

---

## 2. DAX (FDXM / FDAX)

### Instrument
- **1 FDAX** = 25 EUR/pt (lot plein)
- Ou 2 Mini-DAX (FDXM) = 10 EUR/pt

### Horaires (heure de Paris / CET)
- **Debut des trades** : 10h00 CET
- **Sortie forcee (FLAT)** : cloture cash ~17h25 CET (ou fin des donnees)

### Conditions d'entree (identiques au NQ)

| # | Condition | Detail |
|---|-----------|--------|
| 1 | **Alignement M5** | Cloture bougie 5min > SMA20 M5 (LONG) ou < SMA20 M5 (SHORT) |
| 2 | **Alignement H1** | Cloture bougie 1H > SMA20 H1 (LONG) ou < SMA20 H1 (SHORT) |
| 3 | **Distance H1 >= 75 pts** | abs(cloture H1 - SMA20 H1) >= 75 points |
| 4 | **Pullback recent** | Dans les 10 dernieres bougies 5min, le prix a touche la SMA20 M5 (a 15 pts pres) |

### Gestion de position (OPTIMISEE DAX — TRAILING ASYMETRIQUE)

| Parametre | Valeur NQ | **Valeur DAX** | Pourquoi |
|-----------|-----------|----------------|----------|
| Take Profit | 300 pts | **150 pts** | ATR DAX (~250pts) < ATR NQ (~400pts), TP 300 = trop loin |
| Stop Loss max | 200 pts | **Aucun** | Le trailing asymetrique gere tout |
| Trailing LONG | 20 bars M5 | **Low 3 bars - 45 pts** | Stop plus large a l'achat, laisse respirer |
| Trailing SHORT | 20 bars M5 | **High 5 bars + 13 pts** | Stop serre a la vente |
| Daily cap | $1,000 | **Aucun** | Le trailing asymetrique suffit |
| Sortie temps | 20h39 | **~17h25 CET** | Fin session cash DAX |

### Risk Management

| Parametre | Valeur |
|-----------|--------|
| Max trades / jour | 4 |
| Stop apres X pertes consecutives | 3 |

### Parametres moteur
```python
MM20BacktestEngine(
    tp_points=150,              # DAX: TP reduit (ATR plus faible)
    trail_bars=3,               # LONG: low des 3 dernieres bougies
    trail_delta_long=45,        # LONG: -45 pts sous le low (stop large)
    trail_bars_short=5,         # SHORT: high des 5 dernieres bougies
    trail_delta_short=13,       # SHORT: +13 pts au-dessus du high (stop serre)
    max_sl_pts=0,               # Pas de SL fixe, le trailing gere
    max_trades_day=4,
    sma_period=20,
    start_offset_min=0,
    abs_start_hour=10,          # 10h CET
    abs_start_min=0,
    daily_loss_stop=3,
    point_value=10.0,           # 2 FDXM (ou 25.0 pour 1 FDAX)
    daily_loss_usd=0,           # Pas de daily cap
    pullback_bars=10,
    pullback_dist=15,
    min_h1_sma_dist=75,
)
```

### Resultats backtest (12 mois, mars 2025 - mars 2026, 2 FDXM = 10 EUR/pt)
- **533 trades** | WR 43.2% | **PF 1.85** | PnL total : **+80,150 EUR**
- **Max drawdown : 5,660 EUR**
- Mois 2026 : Jan +4,990 | Fev +8,220 | Mars +480 (3/3 verts)

### Donnees requises
- **IMPERATIF** : utiliser les donnees **cash-only** (8h-17h30 CET)
- Les donnees 24h (overnight) faussent la SMA20 avec des barres de faible volume
- Source: Databento FDXM.c.0 sur XEUR.EOBI, filtre horaire applique

---

## 3. DIFFERENCES CLES NQ vs DAX

| | NQ | DAX |
|---|---|---|
| ATR journalier | ~400 pts (2.0%) | ~250 pts (1.1%) |
| TP | 300 pts | **150 pts** |
| SL / Trailing | SL 200 + trail 20 bars | **Trail asymetrique (L:3b-45 / S:5b+13)** |
| Daily cap | $1,000 | **Aucun** |
| Horaires | 16h-20h39 Paris | 10h-17h25 CET |
| Donnees | 24h (futures US) | **Cash-only** (8h-17h30) |
| PF (backtest) | 3.72 (5 ans) | **1.85** (12 mois) |

---

## 4. LOGIQUE VISUELLE (comment lire le graphique)

### On ACHETE (LONG) quand :
```
Prix ──────────────── au-dessus
                        ↕ pullback vers SMA (touche a 15pts)
SMA20 M5 ─────────── ligne bleue
```
+ Le H1 confirme : prix H1 **au-dessus** de SMA20 H1 avec **>= 75 pts d'ecart**

### On VEND (SHORT) quand :
```
SMA20 M5 ─────────── ligne bleue
                        ↕ pullback vers SMA (touche a 15pts)
Prix ──────────────── en-dessous
```
+ Le H1 confirme : prix H1 **en-dessous** de SMA20 H1 avec **>= 75 pts d'ecart**

### On NE FAIT RIEN quand :
- Prix colle a la SMA20 H1 (distance < 75 pts) → **PAS DE TREND**
- Prix loin de la SMA20 M5 sans pullback recent → **PAS DE PULLBACK**
- Les 2 timeframes ne sont pas alignes (M5 au-dessus, H1 en-dessous) → **CONFLIT**
- Hors horaires de trading
- Daily loss cap atteint
- 3 pertes consecutives dans la journee
