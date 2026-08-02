# Railway setup — dashboard steps

One project, two services: **Postgres** (plugin) and **hermes** (this repo). ONE service
for the app — no worker, no cron.

## 1. Create / link

1. Railway → New Project → **Deploy from GitHub repo** → select this repo, branch `main`.
2. Add **PostgreSQL** to the same project (New → Database → PostgreSQL).
3. Railway auto-deploys `main` on every push. CI never deploys.

## 2. Variables (hermes service → Variables)

Set `DATABASE_URL` as a **reference variable** so it resolves to the internal host
(faster, free — never use the public URL from inside the service):

```
DATABASE_URL = ${{ Postgres.DATABASE_URL }}
```

Then add every variable from `.env.example`. Secrets go here and only here — never in the
repo, never over chat. **Rotate anything that has ever been shared over chat or email
before pasting it here** (Postgres password, Stripe whsec, Telegram token, Billplz
X-Signature key).

`PUBLIC_BASE_URL` cannot be set until after the first deploy (step 4).

## 3. Build & deploy configuration

Already committed — no dashboard action needed:

- `railway.json`: NIXPACKS builder, start
  `uvicorn app.main:app --host 0.0.0.0 --port $PORT`, health check `/healthz`,
  restart ON_FAILURE (max 3), and **pre-deploy `alembic upgrade head`** — migrations run
  in the release step, never at import.
- `nixpacks.toml`: installs ffmpeg.

## 4. After the first deploy

1. Settings → Networking → **Generate Domain**. That domain is your `PUBLIC_BASE_URL` —
   set the variable and redeploy.
2. Register webhooks (they could not exist before the domain did):
   - **Stripe** → Developers → Webhooks → **Add endpoint**:
     `https://<domain>/webhooks/stripe`, event `checkout.session.completed`.
     This is a NEW endpoint, separate from the existing `artec.my/stripe-webhook.php` —
     do not reuse or modify that one. Stripe delivers to both. Copy the NEW endpoint's
     `whsec_…` into `STRIPE_WEBHOOK_SECRET`.
   - **Billplz** → collection callback URL: `https://<domain>/webhooks/billplz`.
3. On artec.my: append `?client_reference_id={post_id}` to the Stripe payment link
   (post_id = the spine URL's `utm_campaign` value). For MY, checkout.php POSTs an
   `order_created` event to `https://<domain>/event` at bill creation carrying bill_id +
   post_id; the paid callback is forwarded to `/webhooks/billplz` verbatim (x_signature
   intact). Without these the revenue lane stays permanently empty.
4. Point the site's `/event` beacon at `https://<domain>/event` (CORS allows the
   artec.my origin only).

## 5. Verify

```bash
railway run artec doctor     # or run it in a one-off shell on the service
curl https://<domain>/healthz
```
