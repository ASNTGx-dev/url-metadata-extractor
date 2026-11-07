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

## Deploying on Render (free tier)

Render deploys from this repo via `render.yaml`. Set the
`RAPIDAPI_PROXY_SECRET` environment variable in the Render dashboard to the
secret from the RapidAPI Hub listing (a temporary test value works for
pre-listing checks; replace it with the real proxy secret once listed).

## Safety limits

- URL must be http/https; URLs with embedded credentials are rejected.
- The hostname is resolved with DNS and every resolved address must be
  public. Private (RFC1918), loopback, link-local, multicast, reserved and
  unspecified addresses are rejected, so the API cannot probe internal
  services.
- Fetch: 10 s timeout, max 2 MB response, max 5 redirects, normal browser
  User-Agent.
- Rate limit: 100 requests/hour per caller key (429 with `Retry-After`).
