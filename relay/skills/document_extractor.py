"""`document_extractor`: OCR text becomes validated fields (PRD 13.2).

Local parsing and validators do the work. A model only sees masked text, and only when validators
fail. Step output is stored unencrypted, so raw identifiers leave here only inside `sealed`, an
encrypted blob a rule can open with `open_sealed`; `fields` holds masked forms that are safe to show.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

from relay.core.engine.ports import SkillOutput
from relay.core.errors import LowConfidence
from relay.db.crypto import decrypt, encrypt
from relay.models.recognizers import verhoeff_is_valid
from relay.models.types import ChatMessage, ModelClient, ModelRequest, ModelUnavailable
from relay.skills.base import SkillManifest, load_manifest, load_prompt

_PAN = re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b")
_AADHAAR = re.compile(r"\b[2-9]\d{3}[ -]?\d{4}[ -]?\d{4}\b")
_IFSC = re.compile(r"\b[A-Z]{4}0[A-Z0-9]{6}\b")
_ACCOUNT = re.compile(r"(?:a/?c|account)(?:\s*(?:no|number))?\.?\s*[:#-]?\s*(\d{9,18})\b", re.I)
_NAME = re.compile(r"^\s*name\s*[:\-]\s*(.+?)\s*$", re.I | re.M)
_DOB = re.compile(
    r"(?:dob|date of birth|birth date)\s*[:\-]?\s*(\d{1,2}[/-]\d{1,2}[/-]\d{4}|\d{4}-\d{2}-\d{2})",
    re.I,
)

# What each category should yield, weighted by how much the cross-check relies on it.
_REQUIRED: dict[str, dict[str, float]] = {
    "pan": {"pan": 3, "name": 2, "dob": 1},
    "aadhaar": {"aadhaar": 3, "name": 2, "dob": 1},
    "bank_proof": {"account": 3, "ifsc": 2},
}


def _is_valid_pan(value: str) -> bool:
    return bool(_PAN.fullmatch(value))


def _is_valid_aadhaar(value: str) -> bool:
    digits = re.sub(r"\D", "", value)
    return len(digits) == 12 and digits[0] in "23456789" and verhoeff_is_valid(digits)


def _is_valid_ifsc(value: str) -> bool:
    return bool(_IFSC.fullmatch(value))


def _is_valid_account(value: str) -> bool:
    return value.isdigit() and 9 <= len(value) <= 18


def _is_valid_name(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z][A-Za-z .'-]{1,99}", value)) and " " in value.strip()


def _is_valid_dob(value: str) -> bool:
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            born = datetime.strptime(value, fmt).date()
        except ValueError:
            continue
        # Someone joining today is an adult and was born after 1940.
        return date(1940, 1, 1) <= born <= date(date.today().year - 18, 12, 31)
    return False


VALIDATORS = {
    "pan": _is_valid_pan,
    "aadhaar": _is_valid_aadhaar,
    "ifsc": _is_valid_ifsc,
    "account": _is_valid_account,
    "name": _is_valid_name,
    "dob": _is_valid_dob,
}


def parse_fields(category: str, text: str) -> dict[str, str]:
    """Pull the fields a category should have out of OCR text, without judging them."""
    found: dict[str, str] = {}
    patterns: dict[str, re.Pattern[str]] = {
        "pan": _PAN,
        "aadhaar": _AADHAAR,
        "ifsc": _IFSC,
    }
    for field, weight in _REQUIRED.get(category, {}).items():
        del weight
        if field in patterns:
            match = patterns[field].search(text)
            if match:
                found[field] = match.group(0)
        elif field == "account":
            match = _ACCOUNT.search(text)
            if match:
                found[field] = match.group(1)
        elif field == "name":
            match = _NAME.search(text)
            if match:
                found[field] = match.group(1)
        elif field == "dob":
            match = _DOB.search(text)
            if match:
                found[field] = match.group(1)
    return found


def sidecar_fields(category: str, sidecar: Mapping[str, Any]) -> dict[str, str]:
    """Take the fields a category needs from a sidecar. Seed data says `full_name`."""
    aliases = {"full_name": "name", "bank_account": "account"}
    renamed = {aliases.get(key, key): value for key, value in sidecar.items()}
    return {
        field: str(renamed[field]) for field in _REQUIRED.get(category, {}) if renamed.get(field)
    }


def validate_fields(category: str, fields: Mapping[str, Any]) -> dict[str, bool]:
    """Run the deterministic validator for each field a category requires."""
    return {
        field: bool(VALIDATORS[field](str(fields[field]))) if fields.get(field) else False
        for field in _REQUIRED.get(category, {})
    }


def field_confidence(category: str, validation: Mapping[str, bool]) -> float:
    """Return the weighted share of required fields that pass (PRD 13.3)."""
    weights = _REQUIRED.get(category, {})
    total = sum(weights.values())
    if not total:
        return 0.0
    return sum(w for field, w in weights.items() if validation.get(field)) / total


def mask_value(field: str, value: str) -> str:
    """Return a form safe to store unencrypted and to show on a task."""
    if field in ("name", "ifsc"):
        return value  # not sensitive columns in PRD 10.1
    if field == "dob":
        return "••/••/" + value[-4:]
    digits = re.sub(r"[ -]", "", value)
    return "•" * max(len(digits) - 4, 0) + digits[-4:]


def seal(fields: Mapping[str, str]) -> str:
    """Encrypt raw fields for a later rule, such as the identity cross-check."""
    return encrypt(json.dumps(dict(fields), sort_keys=True))


def open_sealed(blob: str) -> dict[str, str]:
    """Decrypt a blob from `seal`. For rules only: the result must not be stored or logged."""
    opened: dict[str, str] = json.loads(decrypt(blob))
    return opened


class DocumentExtractorSkill:
    """Implements `SkillPort`."""

    name = "document_extractor"

    def __init__(self, router: ModelClient, manifest: SkillManifest | None = None) -> None:
        """Bind the router. The manifest defaults to `definitions/skills/document_extractor.yaml`."""
        self.manifest = manifest or load_manifest(self.name)
        self.allowed_actions = list(self.manifest.allowed_actions)
        self._router = router
        self._prompt = load_prompt(self.manifest)

    def _threshold(self) -> float:
        return self.manifest.threshold if self.manifest.threshold is not None else 0.8

    def _ask_model(
        self, category: str, text: str, parsed: dict[str, str], run_id: Any, step_run_id: Any
    ) -> tuple[dict[str, str], str, str] | None:
        """Ask for the fields that failed. Returns (merged fields, model, prompt version), or None."""

        def merged_confidence(output: dict[str, Any]) -> float:
            merged = {**parsed, **{k: str(v) for k, v in output.items() if v}}
            return field_confidence(category, validate_fields(category, merged))

        try:
            result = self._router.complete(
                ModelRequest(
                    skill=self.name,
                    skill_version=str(self.manifest.version),
                    prompt_version=self.manifest.prompt_version,
                    messages=[
                        ChatMessage(role="system", content=self._prompt),
                        ChatMessage(role="user", content=f"Document type: {category}\n\n{text}"),
                    ],
                    output_schema=self.manifest.output_schema,
                    run_id=run_id,
                    step_run_id=step_run_id,
                    confidence=merged_confidence,
                    threshold=self._threshold(),
                    max_output_tokens=self.manifest.max_output_tokens,
                )
            )
        except (LowConfidence, ModelUnavailable):
            return None
        merged = {**parsed, **{k: str(v) for k, v in result.output.items() if v}}
        return merged, result.model_id, result.prompt_version

    def run(self, inputs: Mapping[str, Any], *, run_id: Any, step_run_id: Any) -> SkillOutput:
        """Extract from each `{category, text}` in `inputs["documents"]`.

        The overall confidence is the weakest document's. A low score reaches the policy engine's
        confidence gate, which sends the step to a person.
        """
        fields: dict[str, dict[str, str]] = {}
        sealed: dict[str, str] = {}
        per_field: dict[str, dict[str, float]] = {}
        validation_out: dict[str, dict[str, bool]] = {}
        scores: list[float] = []
        model_id: str | None = None
        prompt_version: str | None = None
        for document in inputs["documents"]:
            category = str(document["category"])
            # A text-layer sidecar already has fields (PRD 27.4); OCR text has to be parsed.
            text = str(document.get("text", ""))
            sidecar = document.get("fields")
            parsed = sidecar_fields(category, sidecar) if sidecar else parse_fields(category, text)
            ocr_confidence = float(document.get("ocr_confidence", 1.0))
            validation = validate_fields(category, parsed)
            score = field_confidence(category, validation)
            if score < 1.0:
                answered = self._ask_model(category, text, parsed, run_id, step_run_id)
                if answered is not None:
                    parsed, model_id, prompt_version = answered
                    validation = validate_fields(category, parsed)
                    score = field_confidence(category, validation)
            good = {k: v for k, v in parsed.items() if validation.get(k)}
            fields[category] = {k: mask_value(k, v) for k, v in good.items()}
            sealed[category] = seal(good)
            # Validators cannot see a misread that still looks valid, so the reader's own doubt
            # caps the score. This is what sends a blurry card to a person.
            score = min(score, ocr_confidence)
            per_field[category] = {
                k: min(1.0, ocr_confidence) if ok else 0.0 for k, ok in validation.items()
            }
            validation_out[category] = validation
            scores.append(score)
        confidence = min(scores) if scores else 0.0
        return SkillOutput(
            output={
                "fields": fields,
                "sealed": sealed,
                "per_field_confidence": per_field,
                "validation": validation_out,
                "confidence": confidence,
            },
            confidence=confidence,
            skill_version=str(self.manifest.version),
            prompt_version=prompt_version,
            model_id=model_id,
        )
