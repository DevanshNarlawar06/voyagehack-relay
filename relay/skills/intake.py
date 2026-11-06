"""`intake`: a chat message becomes an intent and slots (PRD 13.2).

The keyword table answers the common cases with no model. Sensitive matters come first in the table
and are route-only, so a POSH or grievance message can never be handled as anything else.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from relay.core.engine.ports import SkillOutput
from relay.core.errors import LowConfidence
from relay.models.types import ChatMessage, ModelClient, ModelRequest, ModelUnavailable
from relay.skills.base import SkillManifest, load_manifest, load_prompt

UNKNOWN = "unknown"
_TABLE: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (intent, re.compile(pattern, re.I))
    for intent, pattern in (
        ("posh_report", r"\bposh\b|sexual(ly)? harass|inappropriate(ly)? touch"),
        ("whistleblower_report", r"whistle.?blow|report(ing)? (fraud|corruption|bribe)"),
        ("grievance_report", r"\bgrievance\b|file a complaint|formal complaint|bull(y|ied|ying) "),
        ("resign", r"\bresign|quit my job|put(ting)? in my (papers|notice)|serve my notice"),
        ("data_request", r"(show|see|export|delete|erase|correct).{0,20}\bmy (personal )?data\b"),
        (
            "documents_uploaded",
            r"uploaded|submitted my (documents|docs)|sent (the|my) (documents|docs)",
        ),
        (
            "onboarding_status",
            r"onboarding status|status of my onboarding|where (am i|is my onboarding)|how far along",
        ),
        (
            "policy_question",
            r"\b(leaves?|notice period|probation|gratuity|provident fund|\bpf\b|full and final|f&f)\b",
        ),
    )
)


def match_keyword(text: str) -> str | None:
    """Return the first intent whose pattern matches `text`, in table order."""
    for intent, pattern in _TABLE:
        if pattern.search(text):
            return intent
    return None


class IntakeSkill:
    """Implements `SkillPort`."""

    name = "intake"

    def __init__(self, router: ModelClient, manifest: SkillManifest | None = None) -> None:
        """Bind the router. The manifest defaults to `definitions/skills/intake.yaml`."""
        self.manifest = manifest or load_manifest(self.name)
        self.allowed_actions = list(self.manifest.allowed_actions)
        self._router = router
        self._prompt = load_prompt(self.manifest)

    def _output(
        self,
        intent: str,
        confidence: float,
        model_id: str | None = None,
        prompt_version: str | None = None,
    ) -> SkillOutput:
        return SkillOutput(
            output={"intent": intent, "slots": {}, "confidence": confidence},
            confidence=confidence,
            skill_version=str(self.manifest.version),
            prompt_version=prompt_version,
            model_id=model_id,
        )

    def run(self, inputs: Mapping[str, Any], *, run_id: Any, step_run_id: Any) -> SkillOutput:
        """Classify `inputs["text"]`. An unclear message becomes `unknown`, which goes to HR."""
        text = str(inputs["text"])
        keyword = match_keyword(text)
        if keyword is not None:
            return self._output(keyword, 1.0)
        request = ModelRequest(
            skill=self.name,
            skill_version=str(self.manifest.version),
            prompt_version=self.manifest.prompt_version,
            messages=[
                ChatMessage(role="system", content=self._prompt),
                ChatMessage(role="user", content=text),
            ],
            output_schema=self.manifest.output_schema,
            run_id=run_id,
            step_run_id=step_run_id,
            classification_field="intent",
            threshold=self.manifest.threshold,
            max_output_tokens=self.manifest.max_output_tokens,
        )
        try:
            result = self._router.complete(request)
        except (LowConfidence, ModelUnavailable):
            return self._output(UNKNOWN, 0.0)
        if self._router.is_offline(result.tier):
            # The mock's answer is schema-valid noise. The call still exercised costs and logging.
            return self._output(UNKNOWN, 0.0, result.model_id, result.prompt_version)
        return self._output(
            str(result.output["intent"]),
            float(result.confidence or 0.0),
            result.model_id,
            result.prompt_version,
        )
