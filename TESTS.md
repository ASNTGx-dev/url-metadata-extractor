# URL Metadata Extractor — local test results

Date: 2026-11-07 ~01:35 EET. Server: 127.0.0.1:18081, FastAPI/uvicorn,
secret from env `RAPIDAPI_PROXY_SECRET=local-test-secret-7f3a9`
(throwaway local value only; never a real secret).

## Results

| # | Case | Expected | Got | Pass |
|---|---|---|---|---|
| 1 | GET /health (no auth) | 200 `{"status":"ok",...}` | 200, `{"status":"ok","service":"URL Metadata Extractor","version":"1.0.0"}` | PASS |
| 2 | POST /v1/extract `https://example.com`, correct secret | 200 with real fields | 200: title "Example Domain", language "en", word_count 27, final_url https://example.com | PASS |
| 3 | POST /v1/extract `https://www.wikipedia.org`, correct secret | 200 with og tags, description, favicon | 200: title "Wikipedia", description present, 4 og fields (title/type/description/image), favicon `https://www.wikipedia.org/static/favicon/wikipedia.ico`, language "en", word_count 1320 | PASS |
| 4 | POST /v1/extract, wrong secret | 401 | 401 `{"detail":"Missing or invalid X-RapidAPI-Proxy-Secret header."}` | PASS |
| 5 | POST /v1/extract, missing secret | 401 | 401 (same body) | PASS |
| 6 | POST /v1/extract `ftp://example.com/x` | 400 | 400 "URL must use http or https." | PASS |
| 7 | POST /v1/extract `http://127.0.0.1:18081/health` (SSRF) | 400 | 400 "Hostname resolves to a non-public address (127.0.0.1)." | PASS |
| 8 | POST /v1/extract `http://192.168.1.1/admin` (SSRF) | 400 | 400 (non-public address) | PASS |
| 9 | 101 rapid calls as one key (`X-RapidAPI-User: ratelimit-test`) | first 100 × 200, 101st 429 + Retry-After | exactly 100 × 200, then 429 `{"detail":"Rate limit exceeded (100 requests/hour)."}` with header `retry-after: 3536` | PASS |

9/9 passed.

## Regression tests — 2026-11-12 fixes (ChatGPT review "muse (3)")

Date: 2026-11-12 ~13:15 EET. File: `tests/test_api_fixes.py`
(stdlib `unittest` + `TestClient`; HTTP layer faked — no network fetch
leaves the test process; throwaway secret `regression-test-secret`).
Run: `RAPIDAPI_PROXY_SECRET=regression-test-secret
~/workspace/automation/venv/bin/python -m unittest discover -s tests -v`

**12/12 passed**, in 0.09 s:

| # | Case | Result |
|---|---|---|
| F1 | `_extract_metadata`, no icon tag → favicon null | PASS |
| F2 | `_extract_metadata`, `<link rel="icon">` → resolved URL | PASS |
| F3 | `_extract_metadata`, `<link rel="shortcut icon">` → resolved URL | PASS |
| F4 | Endpoint, page without icon → 200, favicon null, title correct | PASS |
| E1 | Target 404 → 404 "page not found" (was 502) | PASS |
| E2 | Target 403 → 403 "blocked" | PASS |
| E3 | Target 401 → 403 "blocked" | PASS |
| E4 | Target 429 → 403 "blocked" | PASS |
| E5 | Target 500 → 502 "target server error" | PASS |
| E6 | Target down (ConnectError) → 502 "down or unreachable", distinct from blocked | PASS |
| E7 | Target timeout → 504 (unchanged) | PASS |
| E8 | Target 200 with icon → 200, favicon resolved (unchanged) | PASS |

## Local smoke test (real network) — 2026-11-12 ~13:20 EET

Local uvicorn on 127.0.0.1:18081, throwaway secret
`smoke-throwaway-20261112`, real fetches through the VM egress proxy:

- `GET /health` → 200 `{"status":"ok",...}`.
- `POST /v1/extract https://example.com` → 200, title "Example
  Domain", favicon `"data:,"` — that is the page's **declared**
  `<link rel="icon" href="data:,">`, not a guess (example.com's current
  HTML declares it). A page with no icon tag returns favicon `null`
  (see F1/F4 above).
- `POST /v1/extract https://example.com/this-page-does-not-exist-12345`
  → **404** `{"detail":"Target returned HTTP 404 (page not found)."}` —
  previously 502.
- `POST /v1/extract` with no secret → 401 (auth enforced).

**Note:** the fixes above are verified on the local instance only. The
live SnapDeploy container still runs the pre-fix code until the
redeploy in the README's "Redeploying" section is done (push to
`ASNTGx-dev/url-metadata-extractor` main, or dashboard/API redeploy).

## Live check (current deployment, pre-fix code) — 2026-11-12 ~13:08 EET

- Wake call `POST https://snapdeploy.dev/api/public/wake/url-metadata-extractor-a51c4`
  → 200 `{"status":"RUNNING","message":"Container is already running"}`
  — the container was already awake, so **no cold start was measured
  this run** (cold-start duration from the last true cold wake,
  2026-11-12: ~75 s to first 200 on /health).
- `GET /health` → 200 `{"status":"ok",...}` in 0.66 s.
- `POST /v1/extract` with no secret → 401 (auth enforced).
- No extraction was run against the live container in this check
  (the real proxy secret is not available to this agent, and the
  console test is an open item for the parent agent).

## Environment quirks found and fixed during testing

1. **DNS returns CGNAT addresses:** the VM's egress proxy resolves every
   public hostname into `198.18.0.0/15`. `ipaddress.is_global` is False for
   that range, so the SSRF check initially rejected all public sites. Fix:
   explicitly allow `198.18.0.0/15` (documented in `app.py`); all other
   non-public ranges stay blocked.
2. **httpx chokes on NO_PROXY:** the sandbox `NO_PROXY` contains bracketed
   IPv6 entries (`[::1]`, `[fd8b:...]`) that httpx's env parser rejects
   (`Invalid port: ':1]'`). Fix: `trust_env=False` and pass the egress proxy
   explicitly from `HTTPS_PROXY`.
3. **MITM egress proxy TLS:** outbound TLS goes through the egress proxy's
   self-signed chain. Fix: verify against the CA bundle at
   `SSL_CERT_FILE` (`/run/hatch/egress-tls/ca-bundle.pem`) when present.
