"""Kagi search + extract provider for hermes-agent's pluggable web_search backend.

Subclasses :class:`agent.web_search_provider.WebSearchProvider`. Uses the
official Kagi Python client (``openapi_client``, published on PyPI as
``kagi-client`` from https://github.com/kagisearch/kagi-openapi-python).

Env vars:

    KAGI_API_KEY      Bearer token for the Kagi v1 API (required).
                      Create one at https://kagi.com/settings/api_keys.
    KAGI_API_BASE_URL Optional override of the default
                      ``https://kagi.com/api/v1`` host (handy for tests).

Both are read through ``get_provider_env``, so a multiplexed gateway uses the
served profile's own values rather than the launch profile's environment.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from agent.web_search_provider import WebSearchProvider, get_provider_env

logger = logging.getLogger(__name__)

_DEFAULT_HOST = "https://kagi.com/api/v1"
_EXTRACT_BATCH = 10  # Kagi accepts 1-10 pages per /extract call
_MISSING_KEY = (
    "KAGI_API_KEY environment variable not set. "
    "Create a key at https://kagi.com/settings/api_keys"
)
_MISSING_CLIENT = (
    "Kagi Python client (kagi-client) is not installed in Hermes' environment. "
    "Run `hermes pm install` or re-enable the web-kagi plugin."
)


def _build_config():
    """Build an ``openapi_client.Configuration`` from the profile's env.

    Raises ``ValueError`` if ``KAGI_API_KEY`` is unset or the client is
    missing. Defers the ``openapi_client`` import so the plugin module can
    still be loaded when the client isn't installed yet.
    """
    api_key = get_provider_env("KAGI_API_KEY")
    if not api_key:
        raise ValueError(_MISSING_KEY)

    try:
        import openapi_client  # type: ignore[import-not-found]
    except ImportError as exc:
        raise ValueError(_MISSING_CLIENT) from exc

    host = get_provider_env("KAGI_API_BASE_URL") or _DEFAULT_HOST
    return openapi_client.Configuration(host=host, access_token=api_key)


def _page_error(url: str, error: str) -> Dict[str, Any]:
    return {"url": url, "title": "", "content": "", "raw_content": "", "error": error,
            "metadata": {"sourceURL": url}}


def _normalize_search(response: Any) -> Dict[str, Any]:
    """Map ``Search200Response`` to the hermes web-search contract."""
    web_results: List[Dict[str, Any]] = []
    data = getattr(response, "data", None)
    items = getattr(data, "search", None) or []
    for i, result in enumerate(items):
        web_results.append(
            {
                "title": getattr(result, "title", "") or "",
                "url": getattr(result, "url", "") or "",
                "description": getattr(result, "snippet", "") or "",
                "position": i + 1,
            }
        )
    return {"success": True, "data": {"web": web_results}}


def _normalize_extract(response: Any) -> List[Dict[str, Any]]:
    """Map ``ExtractResponse`` to the hermes web-extract contract."""
    documents: List[Dict[str, Any]] = []
    for page in (getattr(response, "data", None) or []):
        url = getattr(page, "url", "") or ""
        markdown = getattr(page, "markdown", None) or ""
        documents.append(
            {
                "url": url,
                "title": "",
                "content": markdown,
                "raw_content": markdown,
                "metadata": {"sourceURL": url},
            }
        )
    for err in (getattr(response, "errors", None) or []):
        documents.append(
            _page_error(
                getattr(err, "url", "") or "",
                getattr(err, "msg", None) or getattr(err, "message", None) or "extraction failed",
            )
        )
    return documents



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
            config = _build_config()
            import openapi_client  # type: ignore[import-not-found]
            from openapi_client.models.search_request import SearchRequest  # type: ignore[import-not-found]

            req_kwargs: Dict[str, Any] = {"query": query}
            if limit:
                req_kwargs["limit"] = max(1, min(int(limit), 1024))

            logger.info("Kagi search: '%s' (limit=%d)", query, limit)
            with openapi_client.ApiClient(config) as api_client:
                api = openapi_client.SearchApi(api_client)
                response = api.search(SearchRequest(**req_kwargs))
            return _normalize_search(response)
        except ValueError as exc:
            return {"success": False, "error": str(exc)}
        except Exception as exc:  # noqa: BLE001
            logger.warning("Kagi search error: %s", exc)
            return {"success": False, "error": f"Kagi search failed: {exc}"}

    def extract(self, urls: List[str], **kwargs: Any) -> List[Dict[str, Any]]:
        """Extract content from one or more URLs via Kagi's ``/extract`` endpoint.

        Kagi accepts 1-10 URLs per call; this method chunks larger lists, and a
        failed batch reports its own URLs as errors without dropping the
        others. Per-URL failures are returned as documents with an ``error``
        field rather than raising. Unknown ``kwargs`` (``format``, ...) are
        ignored, as the ABC requires.
        """
        if not urls:
            return []

        fetchable = list(urls)
        documents: List[Dict[str, Any]] = []

        try:
            config = _build_config()
            import openapi_client  # type: ignore[import-not-found]
            from openapi_client.models.extract_request import ExtractRequest  # type: ignore[import-not-found]
        except ValueError as exc:
            return documents + [_page_error(u, str(exc)) for u in fetchable]
        except Exception as exc:  # noqa: BLE001
            logger.warning("Kagi extract setup error: %s", exc)
            return documents + [_page_error(u, f"Kagi extract failed: {exc}") for u in fetchable]

        with openapi_client.ApiClient(config) as api_client:
            api = openapi_client.ExtractApi(api_client)
            for start in range(0, len(fetchable), _EXTRACT_BATCH):
                chunk = fetchable[start : start + _EXTRACT_BATCH]
                logger.info("Kagi extract: %d URL(s)", len(chunk))
                try:
                    response = api.extract_content(ExtractRequest(pages=[{"url": u} for u in chunk]))
                    documents.extend(_normalize_extract(response))
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
                {
                    "key": "KAGI_API_KEY",
                    "prompt": "Kagi API key",
                    "url": "https://kagi.com/settings/api_keys",
                },
            ],
        }
