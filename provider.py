"""Kagi search + extract provider for hermes-agent's pluggable web_search backend.

Talks to the Kagi v1 API (https://kagi.com/api/docs/openapi) directly over
``httpx``, which Hermes already ships, so the plugin has no dependencies of its
own. (The generated ``kagi-client`` package lags the live API: it drops the
per-page ``error`` field of /extract and can't parse Kagi's error envelope.)

Endpoints used:

    POST /search   {"query", "limit"}         -> data.search[]
    POST /extract  {"pages": [{"url"}], ...}  -> data[] (markdown | error)

Env vars (read with ``get_provider_env``, so a multiplexed gateway uses the
served profile's values, not the launch profile's environment):

    KAGI_API_KEY      Bearer token for the Kagi v1 API (required).
                      Create one at https://kagi.com/api/keys.
    KAGI_API_BASE_URL Optional override of ``https://kagi.com/api/v1``.
"""

from __future__ import annotations

import html
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from agent.web_search_provider import WebSearchProvider, get_provider_env

logger = logging.getLogger(__name__)

PLUGIN_VERSION = "0.2.0"
_DEFAULT_HOST = "https://kagi.com/api/v1"
_EXTRACT_BATCH = 10           # /extract accepts 1-10 pages per call
_SEARCH_LIMIT_MAX = 1024      # /search "limit" range is 1-1024
_SEARCH_TIMEOUT = 30.0        # client-side HTTP timeouts (seconds)
_EXTRACT_TIMEOUT = 60.0
_KEY_URL = "https://kagi.com/api/keys"
_MISSING_KEY = f"KAGI_API_KEY environment variable not set. Create a key at {_KEY_URL}"
_PAGE_LOCATION = re.compile(r"^pages\[(\d+)\]")
_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")


class KagiError(Exception):
    """A failed Kagi API call, with Kagi's own error details when it sent any."""

    def __init__(self, message: str, *, status: Optional[int] = None,
                 errors: Optional[List[Dict[str, Any]]] = None) -> None:
        super().__init__(message)
        self.status = status
        self.errors = errors or []


def _clean(text: Any) -> str:
    """Kagi titles and snippets carry HTML entities and <strong> highlights; make them plain text."""
    if not text:
        return ""
    return _SPACE.sub(" ", html.unescape(_TAG.sub("", str(text)))).strip()


def _headers(api_key: str) -> Dict[str, str]:
    agent = f"hermes-plugin-kagi/{PLUGIN_VERSION}"
    try:
        from hermes_cli.version_info import hermes_user_agent

        agent = f"{hermes_user_agent('Hermes-Agent')} {agent}"
    except Exception:  # noqa: BLE001 - older Hermes or a stripped install
        pass
    return {"Authorization": f"Bearer {api_key}", "User-Agent": agent,
            "Accept": "application/json", "Content-Type": "application/json"}


def _error_details(body: Any) -> List[Dict[str, Any]]:
    """Kagi sends ``errors`` (the spec says ``error``); accept both."""
    if not isinstance(body, dict):
        return []
    details = body.get("errors") or body.get("error") or []
    return [d for d in details if isinstance(d, dict)] if isinstance(details, list) else []


def _error_message(status: int, body: Any, reason: str) -> str:
    details = _error_details(body)
    parts = [str(d.get("message") or d.get("code") or "").strip() for d in details]
    text = "; ".join(p for p in parts if p) or reason or "request failed"
    hint = {401: " (check KAGI_API_KEY)", 403: " (IP address not authorized for this key)",
            429: " (rate limited or usage limit exhausted)"}.get(status, "")
    trace = ((body or {}).get("meta") or {}).get("trace") if isinstance(body, dict) else None
    return f"HTTP {status}: {text}{hint}" + (f" [trace {trace}]" if trace else "")


def _post(path: str, payload: Dict[str, Any], timeout: float) -> Dict[str, Any]:
    """POST to the Kagi API and return the decoded JSON body, or raise :class:`KagiError`."""
    import httpx

    api_key = get_provider_env("KAGI_API_KEY")
    if not api_key:
        raise KagiError(_MISSING_KEY)
    host = (get_provider_env("KAGI_API_BASE_URL") or _DEFAULT_HOST).rstrip("/")
    try:
        # follow_redirects=False (httpx's default): the bearer token never goes to another host.
        resp = httpx.post(f"{host}{path}", json=payload, headers=_headers(api_key),
                          timeout=timeout, follow_redirects=False)
    except httpx.HTTPError as exc:
        raise KagiError(f"{type(exc).__name__}: {exc}") from exc
    try:
        body = resp.json()
    except ValueError:
        body = None
    if resp.status_code != 200:
        raise KagiError(_error_message(resp.status_code, body, resp.reason_phrase),
                        status=resp.status_code, errors=_error_details(body))
    if not isinstance(body, dict):
        raise KagiError("Kagi returned a non-JSON response")
    return body


def _page_error(url: str, error: str) -> Dict[str, Any]:
    return {"url": url, "title": "", "content": "", "raw_content": "", "error": error,
            "metadata": {"sourceURL": url}}


def _document(url: str, markdown: str) -> Dict[str, Any]:
    return {"url": url, "title": "", "content": markdown, "raw_content": markdown,
            "metadata": {"sourceURL": url}}


def _normalize_search(body: Dict[str, Any], limit: int) -> Dict[str, Any]:
    """Map Kagi's ``data.search[]`` (the primary web results) to the hermes web-search contract."""
    items = ((body.get("data") or {}).get("search") or [])[: max(1, limit)]
    return {
        "success": True,
        "data": {
            "web": [
                {
                    "title": _clean(item.get("title")),
                    "url": item.get("url") or "",
                    "description": _clean(item.get("snippet")),
                    "position": i + 1,
                }
                for i, item in enumerate(items) if isinstance(item, dict)
            ]
        },
    }


def _normalize_extract(urls: List[str], body: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Map Kagi's ``data[]`` to hermes extract documents; every requested URL gets an entry."""
    by_url: Dict[str, Dict[str, Any]] = {}
    for page in body.get("data") or []:
        if not isinstance(page, dict):
            continue
        url = page.get("url") or ""
        markdown = page.get("markdown")
        if page.get("error") or not markdown:
            by_url[url] = _page_error(url, str(page.get("error") or "Kagi returned no content"))
        else:
            by_url[url] = _document(url, markdown)
    for i, detail in enumerate(_error_details(body)):
        url = _url_at(urls, detail.get("location")) or f"#error-{i}"
        by_url.setdefault(url, _page_error(url, str(detail.get("message") or detail.get("code"))))
    docs = [by_url.pop(u, None) or _page_error(u, "Kagi returned no result for this URL") for u in urls]
    return docs + list(by_url.values())


def _url_at(urls: List[str], location: Any) -> Optional[str]:
    match = _PAGE_LOCATION.match(str(location or ""))
    if match and int(match.group(1)) < len(urls):
        return urls[int(match.group(1))]
    return None


def _extract_batch(urls: List[str]) -> List[Dict[str, Any]]:
    """One /extract call. Kagi rejects the whole batch (HTTP 400) when any URL is invalid,
    naming it by ``pages[i]``; those URLs become per-URL errors and the rest is retried once."""
    try:
        return _normalize_extract(urls, _post("/extract", {"pages": [{"url": u} for u in urls]}, _EXTRACT_TIMEOUT))
    except KagiError as exc:
        bad: Dict[str, str] = {}
        if exc.status == 400:
            for detail in exc.errors:
                url = _url_at(urls, detail.get("location"))
                if url:
                    bad[url] = str(detail.get("message") or detail.get("code") or exc)
        rest = [u for u in urls if u not in bad]
        if not bad or not rest:
            return [_page_error(u, bad.get(u) or f"Kagi extract failed: {exc}") for u in urls]
        try:
            retried = {d["url"]: d for d in _normalize_extract(rest, _post(
                "/extract", {"pages": [{"url": u} for u in rest]}, _EXTRACT_TIMEOUT))}
        except KagiError as exc2:
            retried = {u: _page_error(u, f"Kagi extract failed: {exc2}") for u in rest}
        return [retried.get(u) or _page_error(u, bad.get(u, "Kagi returned no result for this URL")) for u in urls]


class KagiWebSearchProvider(WebSearchProvider):
    """Kagi search + extract provider."""

    @property
    def name(self) -> str:
        return "kagi"

    @property
    def display_name(self) -> str:
        return "Kagi"

    def is_available(self) -> bool:
        # Cheap, no network: Hermes calls this on every `hermes tools` paint.
        try:
            return bool(get_provider_env("KAGI_API_KEY"))
        except Exception:  # noqa: BLE001 - e.g. no profile scope bound yet
            return False

    def supports_search(self) -> bool:
        return True

    def supports_extract(self) -> bool:
        return True

    def search(self, query: str, limit: int = 5) -> Dict[str, Any]:
        try:
            limit = max(1, min(int(limit or 5), _SEARCH_LIMIT_MAX))
            logger.info("Kagi search: '%s' (limit=%d)", query, limit)
            body = _post("/search", {"query": query, "limit": limit}, _SEARCH_TIMEOUT)
            return _normalize_search(body, limit)
        except KagiError as exc:
            logger.warning("Kagi search error: %s", exc)
            return {"success": False, "error": f"Kagi search failed: {exc}"}
        except Exception as exc:  # noqa: BLE001
            logger.warning("Kagi search error: %s", exc)
            return {"success": False, "error": f"Kagi search failed: {exc}"}

    def extract(self, urls: List[str], **kwargs: Any) -> List[Dict[str, Any]]:
        """Extract markdown from URLs via Kagi's ``/extract``, 10 per call.

        Every URL gets one entry, in order; failures carry an ``error`` field
        instead of raising. Unknown ``kwargs`` (``format``, ...) are ignored,
        as the ABC requires.
        """
        if not urls:
            return []
        if not get_provider_env("KAGI_API_KEY"):
            return [_page_error(u, _MISSING_KEY) for u in urls]
        documents: List[Dict[str, Any]] = []
        for start in range(0, len(urls), _EXTRACT_BATCH):
            chunk = list(urls[start : start + _EXTRACT_BATCH])
            logger.info("Kagi extract: %d URL(s)", len(chunk))
            try:
                documents.extend(_extract_batch(chunk))
            except Exception as exc:  # noqa: BLE001 - one failed batch must not drop the others
                logger.warning("Kagi extract error: %s", exc)
                documents.extend(_page_error(u, f"Kagi extract failed: {exc}") for u in chunk)
        return documents

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": "Kagi",
            "badge": "paid",
            "tag": "Premium search + content extraction. Per-call billing.",
            "env_vars": [
                {"key": "KAGI_API_KEY", "prompt": "Kagi API key", "url": _KEY_URL},
            ],
        }
