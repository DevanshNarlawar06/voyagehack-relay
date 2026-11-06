"""Joiner document upload through a signed one-time link (PRD 16.1, 18).

A joiner has no account, so the link is the credential: HMAC-signed, valid for 72 hours and usable
once. Files are checked by their bytes rather than what they claim to be, and encrypted on disk
outside the web root.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from relay.config import get_settings
from relay.db import models as m
from relay.db.crypto import encrypt

MAX_BYTES = 10 * 1024 * 1024
LINK_LIFETIME = timedelta(hours=72)
_SIGNATURES = {
    b"%PDF-": "application/pdf",
    b"\xff\xd8\xff": "image/jpeg",
    b"\x89PNG\r\n\x1a\n": "image/png",
}


class UploadRefused(Exception):
    """The link or a file is not acceptable. The message is safe to show the joiner."""


class InvalidLink(UploadRefused):
    """The link is forged, expired or already used."""


@dataclass(frozen=True)
class IncomingFile:
    """One uploaded file, read up to one byte past the limit."""

    category: str
    data: bytes
    sidecar: dict[str, Any] | None = None


def sniff_mime(data: bytes) -> str | None:
    """Return the real type from the file's first bytes, or None if it is not PDF, JPG or PNG."""
    for signature, mime in _SIGNATURES.items():
        if data.startswith(signature):
            return mime
    return None


def _sign(body: str) -> str:
    key = hashlib.sha256(f"upload-link:{get_settings().relay_jwt_secret}".encode()).digest()
    return hmac.new(key, body.encode(), hashlib.sha256).hexdigest()


def issue_link(session: Session, person_id: uuid.UUID, run_id: uuid.UUID, now: datetime) -> str:
    """Create a one-time token for a joiner to upload documents for a run."""
    nonce = secrets.token_urlsafe(16)
    expires = now + LINK_LIFETIME
    session.add(m.UploadLink(nonce=nonce, person_id=person_id, run_id=run_id, expires_at=expires))
    session.flush()
    claims = {"n": nonce, "exp": int(expires.timestamp())}
    body = base64.urlsafe_b64encode(json.dumps(claims, sort_keys=True).encode()).decode()
    return f"{body}.{_sign(body)}"


def claim_link(
    session: Session, token: str, now: datetime, *, allow_used: bool = False
) -> m.UploadLink:
    """Check a token and lock its row. Every failure gives the same answer, so nothing leaks.

    `allow_used` accepts a link already spent on the upload, until it expires: the joiner's
    later steps on the same run (policy acknowledgement) reuse it instead of a new credential.
    """
    refused = InvalidLink("This upload link is not valid. Please ask HR for a new one.")
    body, _, signature = token.partition(".")
    if not signature or not hmac.compare_digest(signature, _sign(body)):
        raise refused
    try:
        claims = json.loads(base64.urlsafe_b64decode(body))
    except ValueError:
        raise refused from None
    link = session.scalar(
        select(m.UploadLink).where(m.UploadLink.nonce == claims.get("n")).with_for_update()
    )
    used = link is not None and link.used_at is not None and not allow_used
    if link is None or used or link.expires_at <= now:
        raise refused
    return link


def check_file(incoming: IncomingFile) -> None:
    """Refuse a file that is empty, too big, of an unknown category or of the wrong type."""
    categories = {category.value for category in m.DocumentCategory}
    if incoming.category not in categories:
        raise UploadRefused(f"Unknown document type: {incoming.category[:40]}.")
    if len(incoming.data) > MAX_BYTES:
        raise UploadRefused(f"The {incoming.category} file is larger than 10 MB.")
    if not incoming.data:
        raise UploadRefused(f"The {incoming.category} file is empty.")
    if sniff_mime(incoming.data) is None:
        raise UploadRefused(f"The {incoming.category} file must be a PDF, JPG or PNG.")


def store_file(directory: Path, data: bytes) -> str:
    """Encrypt a file to disk under a random name and return its reference."""
    directory.mkdir(parents=True, exist_ok=True)
    name = uuid.uuid4().hex
    (directory / name).write_text(encrypt(base64.b64encode(data).decode()), encoding="ascii")
    return name


def save_upload(
    session: Session,
    link: m.UploadLink,
    files: list[IncomingFile],
    consents: list[str],
    notice_version: str,
    directory: Path,
    now: datetime,
) -> list[m.Document]:
    """Store files and consents and use up the link, all in the caller's transaction."""
    rows: list[m.Document] = []
    for incoming in files:
        row = m.Document(
            person_id=link.person_id,
            category=m.DocumentCategory(incoming.category),
            storage_ref=store_file(directory, incoming.data),
            extracted=incoming.sidecar,  # encrypted column: a text-layer sidecar (PRD 27.4)
        )
        session.add(row)
        rows.append(row)
    for purpose in consents:
        session.add(
            m.Consent(
                person_id=link.person_id,
                purpose_id=purpose,
                notice_version=notice_version,
                granted_at=now,
                channel="web",
            )
        )
    link.used_at = now
    session.flush()
    return rows
