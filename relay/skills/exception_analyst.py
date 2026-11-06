"""`exception_analyst`: builds the packet a person sees on a conflict or failure (PRD 13.2).

The template is the source of truth for the facts and works with AI off. A model may only rewrite
the summary and suggest one of the options the task really offers.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from relay.core.engine.ports import SkillOutput
from relay.core.errors import LowConfidence
from relay.models.types import ChatMessage, ModelClient, ModelRequest, ModelUnavailable
from relay.skills.base import SkillManifest, load_manifest, load_prompt

_CAUSES = {
    "conflict": "The documents and the record disagree",
    "outage": "An outside system is not responding",
    "exception": "An outside system refused the request",
    "low_confidence": "The extraction could not be verified",
}


def template_packet(inputs: Mapping[str, Any]) -> dict[str, Any]:
    """Build the packet from facts alone. This is also the result when no model is available."""
    kind = str(inputs.get("kind", "exception"))
    reason = str(inputs.get("reason", "No reason was recorded."))
    step = str(inputs.get("step", "this step"))
    fields = [str(f) for f in inputs.get("fields") or []]
    detail = f" Fields in question: {', '.join(fields)}." if fields else ""
    return {
        "summary": f"{step} needs a decision. {reason}{detail}",
        "likely_cause": _CAUSES.get(kind, "Needs review"),
        "suggested_option": None,
        "evidence_refs": [str(ref) for ref in inputs.get("evidence_refs") or []],
    }


class ExceptionAnalystSkill:
    """Implements `SkillPort`."""

    name = "exception_analyst"

    def __init__(self, router: ModelClient, manifest: SkillManifest | None = None) -> None:
        """Bind the router. The manifest defaults to `definitions/skills/exception_analyst.yaml`."""
        self.manifest = manifest or load_manifest(self.name)
        self.allowed_actions = list(self.manifest.allowed_actions)
        self._router = router
        self._prompt = load_prompt(self.manifest)

    def run(self, inputs: Mapping[str, Any], *, run_id: Any, step_run_id: Any) -> SkillOutput:
        """Return `{summary, likely_cause, suggested_option, evidence_refs}`."""
        packet = template_packet(inputs)
        options = [str(option) for option in inputs.get("options") or []]
        facts = "\n".join(f"{k}: {v}" for k, v in sorted((inputs.get("facts") or {}).items()))
        model_id = prompt_version = None
        try:
            result = self._router.complete(
                ModelRequest(
                    skill=self.name,
                    skill_version=str(self.manifest.version),
                    prompt_version=self.manifest.prompt_version,
                    messages=[
                        ChatMessage(role="system", content=self._prompt),
                        ChatMessage(
                            role="user",
                            content=f"{packet['summary']}\nOptions: {options}\nFacts:\n{facts}",
                        ),
                    ],
                    output_schema=self.manifest.output_schema,
                    run_id=run_id,
                    step_run_id=step_run_id,
                    max_output_tokens=self.manifest.max_output_tokens,
                )
            )
        except (LowConfidence, ModelUnavailable):
            result = None
        if result is not None:
            model_id, prompt_version = result.model_id, result.prompt_version
            if not self._router.is_offline(result.tier):
                packet["summary"] = str(result.output["summary"])
                packet["likely_cause"] = str(result.output["likely_cause"])
                suggested = result.output.get("suggested_option")
                # A model cannot invent an option: only the task's own are accepted.
                packet["suggested_option"] = suggested if suggested in options else None
        return SkillOutput(
            output=packet,
            skill_version=str(self.manifest.version),
            prompt_version=prompt_version,
            model_id=model_id,
        )
