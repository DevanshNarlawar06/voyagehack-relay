"""In-memory state for one mock system, including its idempotency behaviour."""

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

Record = dict[str, Any]


class IdempotencyMode(StrEnum):
    """How a system protects against duplicates. Matches `OpSpec.idempotency_mode` (PRD 15.1).

    HEADER: honours `Idempotency-Key`, replaying the stored response.
    REFERENCE_LOOKUP: no protection; the client must look up `client_reference` before retrying.
    NATURAL_KEY: rejects a second resource for the same natural key with 409.
    """

    HEADER = "header"
    REFERENCE_LOOKUP = "reference_lookup"
    NATURAL_KEY = "natural_key"


@dataclass(frozen=True, slots=True)
class StoreResult:
    """Outcome of a write: an HTTP status, a JSON body and whether it replayed an earlier call."""

    status: int
    body: Record
    replayed: bool = False


def body_hash(payload: Record) -> str:
    """Return a stable hash of a JSON payload, used to catch a reused idempotency key."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


@dataclass(slots=True)
class SystemStore:
    """Records of one system. Handlers never await inside a store call, so each call is atomic."""

    id_prefix: str
    mode: IdempotencyMode
    natural_key: str | None = None
    records: dict[str, Record] = field(default_factory=dict)
    _idempotent: dict[str, tuple[str, StoreResult]] = field(default_factory=dict)
    _seq: int = 0
    requests: int = 0
    side_effects: int = 0
    replays: int = 0

    def create(
        self, payload: Record, *, idempotency_key: str | None, bypass_idempotency: bool = False
    ) -> StoreResult:
        """Create a record, honouring this system's idempotency mode unless told to bypass it."""
        check = None if bypass_idempotency else self._check_key(idempotency_key, payload)
        if check is not None:
            return check
        if self.natural_key is not None and not bypass_idempotency:
            value = payload[self.natural_key]
            for existing in self.records.values():
                if existing[self.natural_key] == value:
                    body = {"error": "already_exists", "existing_id": existing["id"]}
                    return StoreResult(409, body)
        self._seq += 1
        record: Record = {
            **payload,
            "id": f"{self.id_prefix}-{self._seq:05d}",
            "status": "active",
            "created_at": datetime.now(UTC).isoformat(),
        }
        self.records[record["id"]] = record
        self.side_effects += 1
        return self._remember(idempotency_key, payload, StoreResult(201, record))

    def act(
        self,
        record_id: str,
        *,
        status: str | None,
        append: tuple[str, str] | None,
        payload: Record,
        idempotency_key: str | None,
        bypass_idempotency: bool = False,
    ) -> StoreResult:
        """Apply a state change or append to a list field of an existing record."""
        record = self.records.get(record_id)
        if record is None:
            return StoreResult(404, {"error": "not_found"})
        check = None if bypass_idempotency else self._check_key(idempotency_key, payload)
        if check is not None:
            return check
        if status is not None and record["status"] != status:
            record["status"] = status
            self.side_effects += 1
        if append is not None:
            name, value = append
            record.setdefault(name, []).append(value)
            self.side_effects += 1
        return self._remember(idempotency_key, payload, StoreResult(200, record))

    def lookup(self, filters: dict[str, str]) -> list[Record]:
        """Return the records whose fields equal every filter value."""
        return [
            r for r in self.records.values() if all(str(r.get(k)) == v for k, v in filters.items())
        ]

    def reset(self) -> None:
        """Forget everything, including counters."""
        self.records.clear()
        self._idempotent.clear()
        self._seq = self.requests = self.side_effects = self.replays = 0

    def _check_key(self, key: str | None, payload: Record) -> StoreResult | None:
        if self.mode is not IdempotencyMode.HEADER or key is None:
            return None
        seen = self._idempotent.get(key)
        if seen is None:
            return None
        stored_hash, result = seen
        if stored_hash != body_hash(payload):
            return StoreResult(422, {"error": "idempotency_key_reused"})
        self.replays += 1
        return StoreResult(result.status, result.body, replayed=True)

    def _remember(self, key: str | None, payload: Record, result: StoreResult) -> StoreResult:
        if self.mode is IdempotencyMode.HEADER and key is not None:
            self._idempotent[key] = (body_hash(payload), result)
        return result
