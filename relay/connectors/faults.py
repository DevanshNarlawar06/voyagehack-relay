"""Client for the mock service's fault injection controls, used by the console in demo mode.

It lives here because only `relay.connectors` may make HTTP calls (PRD 6.4).
"""

from __future__ import annotations

from typing import Any

import httpx

from relay.core.errors import TransientError


class HttpFaultControl:
    """Implements `FaultControl` against `/_control/faults` on the mock service."""

    def __init__(self, base_url: str, transport: httpx.AsyncBaseTransport | None = None) -> None:
        """Bind to the mock service. `transport` lets tests call it in process."""
        self._base_url = base_url.rstrip("/")
        self._transport = transport

    async def _request(self, method: str, json: dict[str, Any] | None = None) -> httpx.Response:
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=5) as client:
                response = await client.request(
                    method, f"{self._base_url}/_control/faults", json=json
                )
        except httpx.TransportError as exc:
            raise TransientError(f"mock control: {type(exc).__name__}") from None
        response.raise_for_status()
        return response

    async def list(self) -> list[dict[str, Any]]:
        """Return the active fault rules."""
        rules: list[dict[str, Any]] = (await self._request("GET")).json()
        return rules

    async def add(self, system: str, fault: str) -> dict[str, Any]:
        """Start injecting `fault` into `system`."""
        rule: dict[str, Any] = (
            await self._request("POST", {"system": system, "fault": fault})
        ).json()
        return rule

    async def clear(self) -> None:
        """Remove every fault rule."""
        await self._request("DELETE")
