"""`comms_drafter`: welcome notes, manager briefs and KT plans (PRD 13.2).

Templates carry every fact and work with AI off. A model only adjusts the wording, is capped at 150
tokens, and sees people's names as tokens.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from relay.core.engine.ports import SkillOutput
from relay.core.errors import LowConfidence
from relay.models.recognizers import Entity
from relay.models.types import ChatMessage, ModelClient, ModelRequest, ModelUnavailable
from relay.skills.base import SkillManifest, load_manifest, load_prompt

TEMPLATES: dict[str, tuple[str, str]] = {
    "manager_brief": (
        "{joiner_name} joins on {joining_date}",
        "{joiner_name} joins your team as {role_title} on {joining_date}. Their buddy is {buddy_name}. "
        "Their laptop, access card and accounts are being arranged, and day-one sessions will be on your calendar.",
    ),
    "welcome": (
        "Welcome to TBO, {joiner_name}",
        "Welcome, {joiner_name}. You start on {joining_date} as {role_title}. {buddy_name} is your buddy. "
        "We will message you here if we need anything else.",
    ),
    "kt_plan": (
        "Knowledge transfer before {last_working_day}",
        "{employee_name}, your last working day is {last_working_day}. Please agree a handover plan with "
        "{manager_name}, covering current work, access and documents.",
    ),
}
_NAME_FIELDS = ("joiner_name", "buddy_name", "employee_name", "manager_name")


class _Blank(dict[str, Any]):
    """Lets a template render when a fact is missing, showing that rather than crashing."""

    def __missing__(self, key: str) -> str:
        return f"[{key} not available]"


class CommsDrafterSkill:
    """Implements `SkillPort`."""

    name = "comms_drafter"

    def __init__(self, router: ModelClient, manifest: SkillManifest | None = None) -> None:
        """Bind the router. The manifest defaults to `definitions/skills/comms_drafter.yaml`."""
        self.manifest = manifest or load_manifest(self.name)
        self.allowed_actions = list(self.manifest.allowed_actions)
        self._router = router
        self._prompt = load_prompt(self.manifest)

    def run(self, inputs: Mapping[str, Any], *, run_id: Any, step_run_id: Any) -> SkillOutput:
        """Return `{subject, body}` for `inputs["template"]`, filled from `inputs["facts"]`."""
        template = str(inputs["template"])
        if template not in TEMPLATES:
            raise ValueError(f"unknown template {template}")
        facts = _Blank(inputs.get("facts") or {})
        subject = TEMPLATES[template][0].format_map(facts)
        body = TEMPLATES[template][1].format_map(facts)
        names = [str(facts[f]) for f in _NAME_FIELDS if f in facts]
        model_id = prompt_version = None
        try:
            result = self._router.complete(
                ModelRequest(
                    skill=self.name,
                    skill_version=str(self.manifest.version),
                    prompt_version=self.manifest.prompt_version,
                    messages=[
                        ChatMessage(role="system", content=self._prompt),
                        ChatMessage(role="user", content=f"Subject: {subject}\n\n{body}"),
                    ],
                    output_schema=self.manifest.output_schema,
                    run_id=run_id,
                    step_run_id=step_run_id,
                    max_output_tokens=self.manifest.max_output_tokens,
                    known_values=[(Entity.PERSON, name) for name in names],
                )
            )
        except (LowConfidence, ModelUnavailable):
            result = None
        if result is not None:
            model_id, prompt_version = result.model_id, result.prompt_version
            if not self._router.is_offline(result.tier):
                subject = str(result.output["subject"])
                body = str(result.output["body"])
        return SkillOutput(
            output={"subject": subject, "body": body},
            skill_version=str(self.manifest.version),
            prompt_version=prompt_version,
            model_id=model_id,
        )
