# DirectQuotes (directquotes.co.za)

Single-page gate motor repair quote funnel: static page + a tiny Python API (standard library only).

On submit it saves the lead to SQLite, emails admin@netictechnologies.com -> info@netictechnologies.com,
and emails the customer a "we'll get a quote to you within 48 hours" confirmation.

## Layout
- `public/` static site served by OpenLiteSpeed
- `server/app.py` API on 127.0.0.1:8099 (`POST /api/quote`, `GET /api/health`)
- `deploy/` systemd unit and server setup
- `deploy.sh` rsync + restart. `.github/workflows/deploy.yml` runs it on push to `main`

## First deploy
1. `./deploy.sh` (needs SSH key access to the VPS).
2. On the VPS edit `/var/www/directquotes/.env` (SMTP_HOST, SMTP_PASS), then `systemctl restart directquotes`.
3. OpenLiteSpeed WebAdmin (port 7080), new Virtual Host `directquotes`:
   - Doc Root `/var/www/directquotes/public/`, domains `directquotes.co.za, www.directquotes.co.za`
   - External App: type Web Server, name `dqapi`, address `127.0.0.1:8099`
   - Context: type Proxy, URI `/api/`, Web Server `dqapi`
   - Map the vhost in the listener and add a Let's Encrypt certificate
4. DNS: A record to 187.124.208.220, proxied through Cloudflare (free plan).
5. In `public/index.html` set `__GOOGLE_ADS_CONVERSION__` (plus your gtag snippet) and `__TURNSTILE_SITE_KEY__`
   (with matching `TURNSTILE_SECRET` in `.env`). Leave Turnstile empty to skip it.

## GitHub Actions
Add repo secrets `VPS_HOST` (187.124.208.220) and `VPS_SSH_KEY` (private deploy key).

## View leads
`sqlite3 /var/www/directquotes/data/leads.db "select * from leads order by id desc limit 20"`
