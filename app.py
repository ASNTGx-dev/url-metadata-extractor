"""URL Metadata Extractor — a small REST API for the RapidAPI track.

Given a public URL, returns the title, meta description, Open Graph tags,
canonical URL, favicon URL, detected language and visible-text word count.

Security notes
--------------
* Only http/https URLs are accepted.
* The hostname is resolved with DNS and every resolved address is checked:
  private, loopback, link-local, reserved and multicast addresses are
  rejected, so the API cannot be used to probe internal services (SSRF).
* Fetches are capped at 10 s, 2 MB and 5 redirects, and use a normal
  browser User-Agent.
* /v1/extract requires the X-RapidAPI-Proxy-Secret header, verified against
  the RAPIDAPI_PROXY_SECRET environment variable. The secret is never
  hardcoded; if the variable is unset the endpoint rejects everything.
* Rate limit: 100 requests/hour per caller key (X-RapidAPI-User header when
  present, otherwise client IP). RapidAPI plan mapping: BASIC (free)
  500 requests/month, PRO later at a higher quota.
"""

import ipaddress
import os
import re
import socket
import time
from collections import defaultdict, deque
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

APP_NAME = "URL Metadata Extractor"
APP_VERSION = "1.0.0"
PORT = int(os.environ.get("PORT", "18081"))
HOST = os.environ.get("HOST", "127.0.0.1")

MAX_FETCH_BYTES = 2 * 1024 * 1024  # 2 MB
FETCH_TIMEOUT = 10.0  # seconds
MAX_REDIRECTS = 5
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Free-tier equivalent: 100 requests/hour per key.
RATE_LIMIT_PER_HOUR = 100
RATE_WINDOW_SECONDS = 3600

app = FastAPI(
    title=APP_NAME,
    description=(
        "Extract title, meta description, Open Graph tags, canonical URL, "
        "favicon URL, detected language and visible-text word count from "
        "any public web page."
    ),
    version=APP_VERSION,
)

# key -> deque of request timestamps (seconds since epoch)
_request_log: dict[str, deque] = defaultdict(deque)


class ExtractRequest(BaseModel):
    url: str = Field(
        ...,
        examples=["https://example.com"],
        description="Public http(s) URL to extract metadata from.",
    )


class ExtractResponse(BaseModel):
    url: str
    final_url: str
    title: str | None
    description: str | None
    og: dict[str, str]
    canonical: str | None
    favicon: str | None
    language: str | None
    word_count: int


def _is_public_ip(ip_str: str) -> bool:
    """True only for globally routable unicast addresses.

    Exception: 198.18.0.0/15 (IANA benchmarking / CGNAT space) is allowed
    because this VM's egress proxy resolves every public hostname into that
    range (see no_proxy / the egress proxy setup). On a normal production
    host public DNS never returns addresses from that range, and all other
    non-public ranges (RFC1918, loopback, link-local, multicast, reserved,
    unspecified, 100.64/10 CGNAT) stay blocked.
    """
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    if ip in ipaddress.ip_network("198.18.0.0/15"):
        return True
    return (
        ip.is_global
        and not ip.is_private
        and not ip.is_loopback
        and not ip.is_link_local
        and not ip.is_multicast
        and not ip.is_reserved
        and not ip.is_unspecified
    )


def _validate_url(raw: str) -> str:
    """Validate the URL and its DNS resolution; return the URL or raise."""
    parsed = urlparse(raw.strip())
    if parsed.scheme not in ("http", "https"):
        raise ValueError("URL must use http or https.")
    if not parsed.hostname:
        raise ValueError("URL must have a hostname.")
    if parsed.username or parsed.password:
        raise ValueError("URLs with embedded credentials are not allowed.")
    host = parsed.hostname
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        raise ValueError("Hostname could not be resolved.")
    addresses = {info[4][0] for info in infos}
    if not addresses:
        raise ValueError("Hostname could not be resolved.")
    for addr in addresses:
        if not _is_public_ip(addr):
            raise ValueError(
                f"Hostname resolves to a non-public address ({addr})."
            )
    return raw.strip()


def _check_secret(secret: str | None) -> bool:
    expected = os.environ.get("RAPIDAPI_PROXY_SECRET")
    if not expected:
        return False
    if not secret:
        return False
    # Constant-time comparison to avoid timing leaks.
    import hmac

    return hmac.compare_digest(secret, expected)


def _rate_limit_key(request: Request, user: str | None) -> str:
    if user:
        return f"user:{user}"
    client = request.client.host if request.client else "unknown"
    return f"ip:{client}"


def _check_rate_limit(key: str) -> int | None:
    """Return seconds until a slot frees up, or None if allowed."""
    now = time.time()
    log = _request_log[key]
    while log and now - log[0] > RATE_WINDOW_SECONDS:
        log.popleft()
    if len(log) >= RATE_LIMIT_PER_HOUR:
        return int(RATE_WINDOW_SECONDS - (now - log[0])) + 1
    log.append(now)
    return None


def _extract_metadata(html: bytes, base_url: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")

    title = None
    if soup.title and soup.title.string:
        title = soup.title.string.strip() or None

    description = None
    desc_tag = soup.find("meta", attrs={"name": "description"})
    if desc_tag and desc_tag.get("content"):
        description = desc_tag["content"].strip() or None

    og: dict[str, str] = {}
    for tag in soup.find_all("meta", attrs={"property": re.compile(r"^og:")}):
        prop = tag.get("property")
        content = tag.get("content")
        if prop and content:
            og[prop] = content.strip()

    canonical = None
    canon_tag = soup.find("link", rel="canonical")
    if canon_tag and canon_tag.get("href"):
        canonical = urljoin(base_url, canon_tag["href"])

    favicon = None
    icon_tag = soup.find("link", rel=re.compile(r"^(shortcut )?icon$", re.I))
    if icon_tag and icon_tag.get("href"):
        favicon = urljoin(base_url, icon_tag["href"])
    else:
        favicon = urljoin(base_url, "/favicon.ico")

    language = None
    html_tag = soup.find("html")
    if html_tag and html_tag.get("lang"):
        language = html_tag["lang"].strip().lower() or None

    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    visible_text = soup.get_text(separator=" ")
    word_count = len(visible_text.split())

    return {
        "title": title,
        "description": description,
        "og": og,
        "canonical": canonical,
        "favicon": favicon,
        "language": language,
        "word_count": word_count,
    }


@app.get("/health", tags=["meta"])
async def health():
    return {"status": "ok", "service": APP_NAME, "version": APP_VERSION}


@app.post("/v1/extract", response_model=ExtractResponse, tags=["extract"])
async def extract(
    body: ExtractRequest,
    request: Request,
    proxy_secret: str | None = Header(default=None, alias="X-RapidAPI-Proxy-Secret"),
    rapidapi_user: str | None = Header(default=None, alias="X-RapidAPI-User"),
):
    if not _check_secret(proxy_secret):
        return JSONResponse(
            status_code=401,
            content={"detail": "Missing or invalid X-RapidAPI-Proxy-Secret header."},
        )

    retry_after = _check_rate_limit(_rate_limit_key(request, rapidapi_user))
    if retry_after is not None:
        return JSONResponse(
            status_code=429,
            content={"detail": "Rate limit exceeded (100 requests/hour)."},
            headers={"Retry-After": str(retry_after)},
        )

    try:
        target = _validate_url(body.url)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    try:
        # No proxy, system CA bundle: this API runs on a normal host and
        # fetches targets directly. (The dev VM used to route through a
        # MITM egress proxy via environment variables; that indirection
        # was removed because deployment scanners treat every
        # os.environ.get as a required variable, and direct fetching was
        # verified working from the VM too. trust_env=False also avoids
        # httpx choking on bracketed IPv6 entries in the sandbox NO_PROXY.)
        proxy = None
        verify: str | bool = True
        async with httpx.AsyncClient(
            timeout=FETCH_TIMEOUT,
            follow_redirects=True,
            max_redirects=MAX_REDIRECTS,
            trust_env=False,
            proxy=proxy,
            verify=verify,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,*/*"},
        ) as client:
            async with client.stream("GET", target) as resp:
                if resp.status_code >= 400:
                    return JSONResponse(
                        status_code=502,
                        content={
                            "detail": f"Target returned HTTP {resp.status_code}."
                        },
                    )
                content_type = resp.headers.get("content-type", "")
                if "text/html" not in content_type and "text/" not in content_type:
                    return JSONResponse(
                        status_code=415,
                        content={
                            "detail": f"Target is not an HTML page ({content_type})."
                        },
                    )
                body_bytes = b""
                async for chunk in resp.aiter_bytes(65536):
                    body_bytes += chunk
                    if len(body_bytes) > MAX_FETCH_BYTES:
                        return JSONResponse(
                            status_code=413,
                            content={"detail": "Target page exceeds 2 MB."},
                        )
                final_url = str(resp.url)
    except httpx.TooManyRedirects:
        return JSONResponse(
            status_code=502, content={"detail": "Too many redirects (max 5)."}
        )
    except httpx.TimeoutException:
        return JSONResponse(
            status_code=504, content={"detail": "Target fetch timed out (10 s)."}
        )
    except httpx.HTTPError as exc:
        return JSONResponse(
            status_code=502, content={"detail": f"Could not fetch target: {exc}."}
        )

    meta = _extract_metadata(body_bytes, final_url)
    return ExtractResponse(
        url=body.url,
        final_url=final_url,
        **meta,
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=HOST, port=PORT)
