"""Manifest-driven HTTP connector: the only place Relay makes outbound calls (PRD 6.4)."""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from relay.connectors.base import ConnectorResult, HealthStatus
from relay.connectors.manifest import (
    DEFINITIONS_DIR,
    ConnectorManifest,
    ErrorKind,
    OpSpec,
    load_manifests,
)
from relay.core.errors import ConflictError, PermanentError, RelayStepError, TransientError

_PATH_VARIABLE = re.compile(r"\{(\w+)\}")
_ERRORS: dict[ErrorKind, type[RelayStepError]] = {
    "transient": TransientError,
    "permanent": PermanentError,
    "conflict": ConflictError,
}
_REPLAY_HEADER = "Idempotent-Replayed"


def _classify(status: int, spec: OpSpec) -> ErrorKind:
    """Map an HTTP error status to the taxonomy. A manifest can override any status."""
    if status in spec.error_map:
        return spec.error_map[status]
    if status in (408, 425, 429) or status >= 500:
        return "transient"
    if status == 409:
        return "conflict"
    return "permanent"


class HttpConnector:
    """Implements `Connector` for any system described by a manifest."""

    def __init__(
        self, manifest: ConnectorManifest, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        """Bind a manifest. `transport` lets tests talk to the mock app in process."""
        self._manifest = manifest
        self._transport = transport
        self.name = manifest.connector

    def ops(self) -> dict[str, OpSpec]:
        """Return the operations declared in the manifest."""
        return dict(self._manifest.ops)

    async def call(
        self, op: str, payload: dict[str, Any], *, idempotency_key: str
    ) -> ConnectorResult:
        """Perform `op`, carrying our key in the header and, for creates, in the body.

        A 409 from a natural-key system is checked: if the existing record is ours this is a retry
        that already landed, and if it is someone else's it stays a ConflictError.
        """
        spec = self._spec(op)
        path, remaining = self._render_path(op, spec, payload)
        headers = {"Idempotency-Key": idempotency_key}
        if spec.method == "GET":
            response = await self._send(op, spec, "GET", path, params=remaining, headers=headers)
        else:
            body = {**spec.defaults, **remaining}
            if spec.inject_reference:
                body["client_reference"] = idempotency_key
            response = await self._send(op, spec, "POST", path, json=body, headers=headers)

        if response.status_code == 409 and spec.idempotency_mode == "natural_key":
            found = await self.reconcile(op, idempotency_key, payload)
            if found is not None:
                return found
        if response.is_error:
            self._raise(op, response.status_code, spec)
        return self._to_result(response, spec)

    async def reconcile(
        self, op: str, idempotency_key: str, payload: dict[str, Any]
    ) -> ConnectorResult | None:
        """Ask the system whether an earlier attempt landed (PRD 11.5 step 2).

        Returns None when it did not, or when the op declares no lookup because it is safe to
        repeat. Raises ConflictError if the natural key belongs to a record we did not create.
        """
        spec = self._spec(op)
        if spec.reconcile is None:
            return None
        by = spec.reconcile.by
        value = idempotency_key if by == "client_reference" else payload.get(by)
        if value is None:
            raise PermanentError(f"{self.name}.{op}: reconcile needs the field {by}")
        response = await self._send(
            op, spec, "GET", spec.reconcile.path, params={by: value}, headers={}
        )
        if response.is_error:
            self._raise(op, response.status_code, spec)
        items: list[dict[str, Any]] = response.json()["items"]
        if not items:
            return None
        ours = [item for item in items if item.get("client_reference") == idempotency_key]
        if not ours:
            raise ConflictError(f"{self.name}.{op}: a record with this {by} is not ours")
        return ConnectorResult(
            external_id=str(ours[0]["id"]),
            response=ours[0],
            reconciled=True,
            duplicates=len(ours) - 1,
            cost_inr=spec.cost_inr,
        )

    async def health(self) -> HealthStatus:
        """Probe the system's health path. Failures are reported, never raised."""
        started = time.perf_counter()
        url = self._manifest.resolved_base_url() + self._manifest.health_path
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=5) as client:
                response = await client.get(url)
        except httpx.HTTPError as exc:
            return HealthStatus(connector=self.name, healthy=False, detail=type(exc).__name__)
        return HealthStatus(
            connector=self.name,
            healthy=response.is_success,
            status_code=response.status_code,
            latency_ms=(time.perf_counter() - started) * 1000,
        )

    def _spec(self, op: str) -> OpSpec:
        spec = self._manifest.ops.get(op)
        if spec is None:
            raise PermanentError(f"{self.name} has no op {op}")
        return spec

    def _render_path(
        self, op: str, spec: OpSpec, payload: dict[str, Any]
    ) -> tuple[str, dict[str, Any]]:
        """Fill `{variables}` in the path from the payload and return the fields left over."""
        names = _PATH_VARIABLE.findall(spec.path)
        missing = [name for name in names if name not in payload]
        if missing:
            raise PermanentError(f"{self.name}.{op}: missing path field {missing[0]}")
        path = _PATH_VARIABLE.sub(lambda m: quote(str(payload[m.group(1)]), safe=""), spec.path)
        return path, {key: value for key, value in payload.items() if key not in names}

    async def _send(
        self, op: str, spec: OpSpec, method: str, path: str, **kwargs: Any
    ) -> httpx.Response:
        url = self._manifest.resolved_base_url() + path
        try:
            async with httpx.AsyncClient(
                transport=self._transport, timeout=spec.timeout_seconds
            ) as client:
                return await client.request(method, url, **kwargs)
        except httpx.TransportError as exc:
            # The outcome is unknown, so the engine retries and the ledger reconciles first.
            raise TransientError(f"{self.name}.{op}: {type(exc).__name__}") from None

    def _raise(self, op: str, status: int, spec: OpSpec) -> None:
        error = _ERRORS[_classify(status, spec)]
        raise error(f"{self.name}.{op}: HTTP {status}")

    def _to_result(self, response: httpx.Response, spec: OpSpec) -> ConnectorResult:
        body: dict[str, Any] = response.json()
        identifier = body.get("id")
        return ConnectorResult(
            external_id=str(identifier) if identifier is not None else None,
            response=body,
            replayed=response.headers.get(_REPLAY_HEADER) == "true",
            cost_inr=spec.cost_inr,
        )


def load_connectors(
    directory: Path = DEFINITIONS_DIR, transport: httpx.AsyncBaseTransport | None = None
) -> dict[str, HttpConnector]:
    """Build a connector for every manifest in `directory`, keyed by name."""
    return {
        name: HttpConnector(manifest, transport)
        for name, manifest in load_manifests(directory).items()
    }
