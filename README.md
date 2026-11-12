# URL Metadata Extractor

A small, genuinely useful REST API: give it a public URL, get back the page's
title, meta description, Open Graph tags, canonical URL, favicon URL,
detected language and visible-text word count. It reads only data the site
itself publishes, and wraps no paid API.

## Endpoints

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/health` | none | `{"status":"ok", ...}` liveness check |
| POST | `/v1/extract` | `X-RapidAPI-Proxy-Secret` | Extract metadata from a public URL |

Request body: `{ "url": "https://example.com" }`

## Running locally

```bash
export RAPIDAPI_PROXY_SECRET='<secret from the RapidAPI dashboard>'
./start.sh
# Server binds 127.0.0.1:18081
```

The secret is read only from the `RAPIDAPI_PROXY_SECRET` environment
variable. It is never hardcoded and never stored in this repo.

## Redeploying (SnapDeploy, free tier)

The container is built from the GitHub repo
`ASNTGx-dev/url-metadata-extractor` (branch `main`). Every push to
`main` redeploys automatically via the webhook SnapDeploy installed
(free tier: 10 deploys/day, 5 per rolling 12 hours; failed builds count).
Alternative: the SnapDeploy dashboard Redeploy button, or the API
`POST /api/mobile/github/link/{repoLinkId}/deploy` with a
`deploy`-scoped token from the dashboard's API Keys page. A redeploy
starts the new task beside the old one and switches traffic only after
the new task passes its health check — if the new build never becomes
healthy, the old one keeps serving. The live `RAPIDAPI_PROXY_SECRET`
is already set in the deployment (real RapidAPI proxy secret); a
redeploy keeps the deployment's environment variables, so the secret
does **not** need re-entering.

## Wake-on-demand operating procedure (free tier)

The free-tier container sleeps after ~15 min idle and answers with a
503 "Container is Sleeping" page — a plain request does **not** wake
it, and scripts/cron/CI do not wake it either. Before any audit run
or extraction against the live backend:

1. Wake once (unauthenticated, no body — sending a body fails the call):
   `curl -X POST https://snapdeploy.dev/api/public/wake/url-metadata-extractor-a51c4 -H "Content-Type: application/json"`
   Expect 200 `{"status":"STARTING",...}` (or "already running").
2. Wait ~75 s (cold start is container provisioning, ~60–90 s, not app
   startup — the app itself answers /health in ~1.6 s locally).
3. Verify `GET /health` returns 200
   `{"status":"ok","service":"URL Metadata Extractor","version":"1.0.0"}`.
4. Run the extraction(s) via the RapidAPI proxy (or, for a direct
   check, POST /v1/extract with the proxy secret — never share the
   real secret; a request without it must return 401).
5. Record the cold-start duration (wake call → first 200 on /health).

Rules: **never** configure the RapidAPI health check (or anything
else) to call the wake endpoint — the health check must be
`GET /health` only. **Never** use a keep-alive ping: SnapDeploy's
Terms explicitly forbid manufacturing activity to defeat auto-sleep
(penalty: immediate account suspension), and 24/7 wakefulness would
burn the 100 container-hours/month allowance in ~4 days.

## Error behaviour (since 2026-11-12 fixes)

- `favicon` is `null` when the page declares no icon tag — the API
  never guesses `/favicon.ico`.
- Target failures are distinct: target 404 → 404 (page not found);
  target 401/403/429 → 403 (target blocked the request); target 5xx →
  502 (target server error); target down/unreachable → 502 (connection
  failed); target timeout → 504. See `API_README.md` and `tests/`.

## Known limits

- Fetch: 10 s timeout, **max 2 MB response** (larger pages return
  413 "Target page exceeds 2 MB"), max 5 redirects. The 2 MB cap is
  kept deliberately — it bounds memory on a 512 MB free-tier container
  and is documented, not a bug.
- Free-tier limits: sleeps after 15 min idle (~60–90 s to wake); 100
  container-hours/month. A sleeping container answers server-side calls
  with a 503 "Container is Sleeping" page — it does NOT self-wake on a
  plain request. Wake it first with:
  `curl -X POST https://snapdeploy.dev/api/public/wake/url-metadata-extractor-a51c4 -H "Content-Type: application/json"`
  (no body — sending a body makes the call fail), then wait ~75 s.
  A browser visit to the URL wakes it the same way via the page's JS.
- `RAPIDAPI_PROXY_SECRET` in the deployment is the real RapidAPI
  proxy secret (replaced the temporary test value on 2026-11-07,
  rolling restart; a request with the old test value returns 401).
  Never put the real secret in this repo.

## RapidAPI listing (live since 2026-11-07)

- Public listing: https://rapidapi.com/Bob-8L4AnmUt_/api/url-metadata-extractor15
  (provider Bob-8L4AnmUt_, category Data, version 1.0.0). BASIC plan free,
  500 requests/month; no paid plan. RapidAPI health check: SUCCESS.
  End-to-end test via the RapidAPI console: 200, example.com title
  "Example Domain". The real proxy secret replaced the test value in
  SnapDeploy (rolling restart).
- NOTE (corrected 12:19 EET): the listing was actually PRIVATE at first —
  the visibility toggle from the first pass did not persist (the Save click
  was missed), and an anonymous fetch returned "API not found". The publish
  step was then completed properly (ownership checkbox + visibility on +
  Save) and Studio confirmed "API Project is Public" after a fresh reload.
  VERIFIED 12:25 EET: an anonymous curl of the listing URL returns HTTP
  200 with the listing content and no "not found" text — the listing is
  genuinely public.

## Deploying on Render (free tier)

Render deploys from this repo via `render.yaml`. Set the
`RAPIDAPI_PROXY_SECRET` environment variable in the Render dashboard to the
secret from the RapidAPI Hub listing (a temporary test value works for
pre-listing checks; replace it with the real proxy secret once listed).

**Render is currently unusable:** as of Nov 2026 its free tier demands a
payment card (temporary $1 authorisation) before deploying — excluded by
the mission's no-payment-before-earnings rule.

## Safety limits

- URL must be http/https; URLs with embedded credentials are rejected.
- The hostname is resolved with DNS and every resolved address must be
  public. Private (RFC1918), loopback, link-local, multicast, reserved and
  unspecified addresses are rejected, so the API cannot probe internal
  services.
- Fetch: 10 s timeout, max 2 MB response, max 5 redirects, normal browser
  User-Agent.
- Rate limit: 100 requests/hour per caller key (429 with `Retry-After`).
