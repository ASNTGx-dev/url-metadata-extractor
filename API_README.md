# URL Metadata Extractor — API README

A small, genuinely useful REST API: give it a public URL, get back the page's
title, meta description, Open Graph tags, canonical URL, favicon URL,
detected language and visible-text word count. It reads only data the site
itself publishes, and wraps no paid API.

## Running it (locally)

```bash
export RAPIDAPI_PROXY_SECRET='<secret from the RapidAPI dashboard>'
./start.sh
# Server binds 127.0.0.1:18081
```

The secret is read **only** from the `RAPIDAPI_PROXY_SECRET` environment
variable at runtime. It is never hardcoded and never stored in this repo.
`.env.example` shows the variable name with a placeholder value for local
testing only. If the variable is unset, `/v1/extract` rejects every request
with 401.

Dependencies: FastAPI + uvicorn + httpx + beautifulsoup4, installed in
`~/workspace/automation/venv` (Python 3.12).

## Endpoints

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/health` | none | `{"status":"ok", ...}` liveness check |
| POST | `/v1/extract` | `X-RapidAPI-Proxy-Secret` | Extract metadata from a public URL |

### POST /v1/extract

Request body:

```json
{ "url": "https://example.com" }
```

Response (200):

```json
{
  "url": "https://example.com",
  "final_url": "https://example.com",
  "title": "Example Domain",
  "description": null,
  "og": {},
  "canonical": null,
  "favicon": "data:,",
  "language": "en",
  "word_count": 27
}
```

Errors:

| Status | Meaning |
|---|---|
| 400 | Invalid URL (not http/https, unresolvable host, or a non-public address — SSRF protection) |
| 401 | Missing or wrong `X-RapidAPI-Proxy-Secret` |
| 403 | Target blocked the request (target returned 401, 403 or 429) |
| 404 | Target returned HTTP 404 (page not found) |
| 413 | Target page exceeds 2 MB |
| 415 | Target is not an HTML page |
| 429 | Rate limit exceeded (see below) |
| 502 | Target server error (target 5xx), target down/unreachable, or too many redirects |
| 504 | Target fetch timed out (10 s) |

`favicon` is the URL from the page's declared `<link rel="icon">` tag,
resolved against the page URL — or `null` when the page declares no icon.
The API never guesses `/favicon.ico`: a guessed URL that 404s is a false
positive. (Note: example.com currently declares `<link rel="icon"
href="data:,">`, so its favicon is honestly reported as `"data:,"` —
the page's own declared value, not a guess.)

## Safety limits

- URL must be http/https; URLs with embedded credentials are rejected.
- The hostname is resolved with DNS and **every** resolved address must be
  public. Private (RFC1918), loopback, link-local, multicast, reserved and
  unspecified addresses are rejected, so the API cannot probe internal
  services. One documented exception: `198.18.0.0/15` (IANA benchmarking
  space) is allowed because this VM's egress proxy resolves all public
  hostnames into that range; on a normal production host public DNS never
  returns addresses from it.
- Fetch: 10 s timeout, max 2 MB response, max 5 redirects, normal browser
  User-Agent.
- Fetches go direct (no proxy) and TLS is verified against the system CA
  bundle. (An earlier version routed the dev VM's MITM egress proxy via
  environment variables; that was removed because deployment scanners
  treat every `os.environ.get` as a required variable, and direct
  fetching was verified working from the VM too.)

## Rate limits and RapidAPI plan mapping

In-memory limit: **100 requests/hour per caller key** (the
`X-RapidAPI-User` header when present, otherwise the client IP). Exceeding
it returns `429 Too Many Requests` with a `Retry-After` header (seconds).

Intended RapidAPI pricing (to be set at listing time):

| Plan | Quota |
|---|---|
| BASIC (free) | 500 requests/month |
| PRO (later) | Higher quota, price TBD after free-tier traction |

The 100/hour in-app limit is a per-key abuse guard; the monthly plan quota
is enforced by RapidAPI itself at listing time.

## OpenAPI

`openapi.json` is generated from the app (`app.openapi()`). It is the file
to import when creating the RapidAPI listing.

## Future RapidAPI listing steps (NOT done — no account exists yet)

1. Create a RapidAPI provider account (user does this, or authorises it).
2. Create a new API in the RapidAPI Hub and import `openapi.json`.
3. Set the API base URL to this VM's public address (requires exposing port
   18081 through a stable tunnel/reverse proxy — the VM currently binds
   127.0.0.1 only; uptime monitoring to be added before listing).
4. In the Hub, copy the **Proxy Secret** and set it as
   `RAPIDAPI_PROXY_SECRET` on the server, then restart.
5. Configure plans: BASIC free 500/month, PRO later.
6. Add docs, example responses and test the listing end-to-end through the
   RapidAPI proxy.
7. **Only after a payout is actually generated** does the user link a PayPal
   address in the RapidAPI dashboard (RapidAPI emails the provider when a
   payout exists). No payment/payout information is entered before that.
