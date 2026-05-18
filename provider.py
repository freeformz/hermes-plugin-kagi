"""Kagi search + extract provider for hermes-agent's pluggable web_search backend.

Subclasses :class:`agent.web_search_provider.WebSearchProvider`. Uses the
official Kagi Python client (``openapi_client``, generated from the OpenAPI
spec at https://github.com/kagisearch/kagi-openapi-python).

Env vars:

    KAGI_API_KEY      Bearer token for the Kagi v1 API (required).
                      Create one at https://kagi.com/settings/api_keys.
    KAGI_API_BASE_URL Optional override of the default
                      ``https://kagi.com/api/v1`` host (handy for tests).

Install:

    pip install git+https://github.com/kagisearch/kagi-openapi-python.git
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List

from agent.web_search_provider import WebSearchProvider

logger = logging.getLogger(__name__)

_DEFAULT_HOST = "https://kagi.com/api/v1"


def _build_config():
    """Build an ``openapi_client.Configuration`` from env vars.

    Raises ``ValueError`` if ``KAGI_API_KEY`` is unset. Defers the
    ``openapi_client`` import so the plugin module can still be loaded
    when the client isn't installed yet.
    """
    api_key = os.getenv("KAGI_API_KEY", "").strip()
    if not api_key:
        raise ValueError(
            "KAGI_API_KEY environment variable not set. "
            "Create a key at https://kagi.com/settings/api_keys"
        )

    try:
        import openapi_client  # type: ignore[import-not-found]
    except ImportError as exc:
        raise ValueError(
            "Kagi Python client not installed. Run: "
            "pip install git+https://github.com/kagisearch/kagi-openapi-python.git"
        ) from exc

    host = os.getenv("KAGI_API_BASE_URL", "").strip() or _DEFAULT_HOST
    return openapi_client.Configuration(host=host, access_token=api_key)


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
        err_url = getattr(err, "url", "") or ""
        documents.append(
            {
                "url": err_url,
                "title": "",
                "content": "",
                "raw_content": "",
                "error": getattr(err, "msg", None)
                or getattr(err, "message", None)
                or "extraction failed",
                "metadata": {"sourceURL": err_url},
            }
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
        return bool(os.getenv("KAGI_API_KEY", "").strip())

    def supports_search(self) -> bool:
        return True

    def supports_extract(self) -> bool:
        return True

    def search(self, query: str, limit: int = 5) -> Dict[str, Any]:
        try:
            from tools.interrupt import is_interrupted

            if is_interrupted():
                return {"success": False, "error": "Interrupted"}

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

        Kagi accepts 1-10 URLs per call; this method chunks larger lists
        transparently. Per-URL failures are returned as documents with an
        ``error`` field rather than raising.
        """
        if not urls:
            return []

        try:
            from tools.interrupt import is_interrupted

            if is_interrupted():
                return [
                    {"url": u, "title": "", "content": "", "raw_content": "", "error": "Interrupted"}
                    for u in urls
                ]

            config = _build_config()
            import openapi_client  # type: ignore[import-not-found]
            from openapi_client.models.extract_request import ExtractRequest  # type: ignore[import-not-found]

            documents: List[Dict[str, Any]] = []
            with openapi_client.ApiClient(config) as api_client:
                api = openapi_client.ExtractApi(api_client)
                for start in range(0, len(urls), 10):
                    chunk = urls[start : start + 10]
                    logger.info("Kagi extract: %d URL(s)", len(chunk))
                    req = ExtractRequest(pages=[{"url": u} for u in chunk])
                    response = api.extract_content(req)
                    documents.extend(_normalize_extract(response))
            return documents
        except ValueError as exc:
            return [
                {"url": u, "title": "", "content": "", "raw_content": "", "error": str(exc)}
                for u in urls
            ]
        except Exception as exc:  # noqa: BLE001
            logger.warning("Kagi extract error: %s", exc)
            return [
                {"url": u, "title": "", "content": "", "raw_content": "", "error": f"Kagi extract failed: {exc}"}
                for u in urls
            ]

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
