# Guide Stripe — x-trade.ai
## Configuration pas a pas

---

### ETAPE 1 : Creer un compte Stripe (si pas deja fait)

1. Aller sur https://dashboard.stripe.com/register
2. Remplir email, nom, mot de passe
3. Confirmer l'email
4. Completer la verification d'identite (KYC) — obligatoire pour recevoir les paiements

---

### ETAPE 2 : Creer le Produit et le Prix

1. Aller dans **Stripe Dashboard > Products** (https://dashboard.stripe.com/products)
2. Cliquer **+ Add product**
3. Remplir :
   - **Name** : `x-trade.ai Risk Desk`
   - **Description** : `Institutional risk management for prop firm traders. 1 license = 1 prop firm account.`
   - **Image** : (optionnel) upload le logo x-trade.ai
4. Dans la section **Pricing** :
   - **Model** : `Recurring`
   - **Price** : `$29.00`
   - **Billing period** : `Monthly`
   - **Currency** : `USD`
5. Cliquer **Save product**
6. Une fois cree, cliquer sur le produit, puis sur le prix
7. **Copier le Price ID** — il ressemble a `price_1Nxxxxxxxxxxxxx`
8. Le mettre dans ton `.env` :
   ```
   STRIPE_PRICE_ID=price_1Nxxxxxxxxxxxxx
   ```

---

### ETAPE 3 : Recuperer la Secret Key

1. Aller dans **Stripe Dashboard > Developers > API keys** (https://dashboard.stripe.com/apikeys)
2. Copier la **Secret key** (commence par `sk_live_` en production, `sk_test_` en test)
3. La mettre dans ton `.env` :
   ```
   STRIPE_SECRET_KEY=sk_live_xxxxxxxxxxxxxxxx
   ```

**IMPORTANT** : Utilise d'abord les cles TEST (`sk_test_`) pour verifier que tout marche, puis passe en LIVE.

---

### ETAPE 4 : Configurer le Webhook

Le webhook permet a Stripe de notifier x-trade.ai quand un paiement est reussi ou un abonnement annule.

1. Aller dans **Stripe Dashboard > Developers > Webhooks** (https://dashboard.stripe.com/webhooks)
2. Cliquer **+ Add endpoint**
3. Remplir :
   - **Endpoint URL** : `https://x-trade.ai/api/stripe/webhook`
     (ou `https://ton-domaine.com/api/stripe/webhook`)
   - **Events to listen to** : cliquer **Select events**, puis cocher :
     - `checkout.session.completed`
     - `customer.subscription.deleted`
     - `customer.subscription.updated`
     - `invoice.payment_failed`
4. Cliquer **Add endpoint**
5. Une fois cree, cliquer sur le webhook, puis **Reveal signing secret**
6. Copier le **Webhook signing secret** (commence par `whsec_`)
7. Le mettre dans ton `.env` :
   ```
   STRIPE_WEBHOOK_SECRET=whsec_xxxxxxxxxxxxxxxx
   ```

---

### ETAPE 5 : Configurer le Customer Portal (optionnel mais recommande)

Le Customer Portal permet aux clients de gerer leur abonnement eux-memes (annuler, changer de carte, voir les factures).

1. Aller dans **Stripe Dashboard > Settings > Billing > Customer portal**
   (https://dashboard.stripe.com/settings/billing/portal)
2. Activer :
   - **Cancel subscriptions** : Yes
   - **Update payment methods** : Yes
   - **View invoice history** : Yes
3. Sauvegarder

---

### ETAPE 6 : Tester en mode Test

1. Dans le `.env`, mettre les cles TEST :
   ```
   STRIPE_SECRET_KEY=sk_test_xxxxxxxxxxxxxxxx
   STRIPE_PRICE_ID=price_test_xxxxxxxxxxxxxxxx
   STRIPE_WEBHOOK_SECRET=whsec_test_xxxxxxxxxxxxxxxx
   ```
2. Demarrer l'app : `python main.py`
3. Aller sur la page pricing et cliquer "Get Started"
4. Utiliser la carte de test Stripe : `4242 4242 4242 4242`, date future, CVC quelconque
5. Verifier que :
   - Le paiement apparait dans Stripe Dashboard
   - La licence est creee (check `/api/admin/licenses`)
   - Le webhook a ete recu (Stripe Dashboard > Webhooks > Recent events)

---

### ETAPE 7 : Passer en production

1. Remplacer les cles TEST par les cles LIVE dans `.env`
2. Creer un nouveau webhook endpoint avec l'URL de production
3. Mettre a jour `APP_URL` dans `.env` :
   ```
   APP_URL=https://x-trade.ai
   ```
4. Verifier que le domaine est bien configure (DNS, SSL)

---

### RESUME — Variables .env

```
# Stripe
STRIPE_SECRET_KEY=sk_live_xxxxxxxxxxxxxxxx
STRIPE_PRICE_ID=price_1Nxxxxxxxxxxxxx
STRIPE_WEBHOOK_SECRET=whsec_xxxxxxxxxxxxxxxx
APP_URL=https://x-trade.ai
```

---

### COMMANDES UTILES (API x-trade.ai)

**Creer un lot de codes promo pour tes eleves :**
```
curl -X POST "http://localhost:8001/api/admin/promo/batch?count=20&duration_days=60&note=Formation+mars+2026" \
  -H "X-Admin-Key: TA_CLE_ADMIN"
```

**Lister les licences actives :**
```
curl "http://localhost:8001/api/admin/licenses" -H "X-Admin-Key: TA_CLE_ADMIN"
```

**Lister les codes promo :**
```
curl "http://localhost:8001/api/admin/promo/list" -H "X-Admin-Key: TA_CLE_ADMIN"
```

---

### SUPPORT

- Documentation Stripe : https://docs.stripe.com/
- Stripe CLI (pour tester les webhooks en local) : https://docs.stripe.com/stripe-cli
  ```
  stripe listen --forward-to localhost:8001/api/stripe/webhook
  ```
