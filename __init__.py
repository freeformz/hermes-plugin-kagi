"""Kagi web search + content extraction plugin for hermes-agent."""

from __future__ import annotations

from .provider import KagiWebSearchProvider


def register(ctx) -> None:
    """Register the Kagi provider with the plugin context."""
    ctx.register_web_search_provider(KagiWebSearchProvider())
