"""`helpdesk`: policy questions, answered only from retrieved text, with citations (PRD 13.2).

Below the similarity floor it does not guess: it says it will route the question to HR. When no
model is available it answers with the best passage word for word, which is always grounded.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from relay.core.engine.ports import SkillOutput
from relay.core.errors import LowConfidence
from relay.models.types import ChatMessage, ModelClient, ModelRequest, ModelUnavailable
from relay.skills.base import SkillManifest, load_manifest, load_prompt
from relay.skills.retrieval import Hit, Retriever, default_retriever

DEFAULT_FLOOR = 0.5
ROUTE_TO_HR = "I'll route this to HR, because I can't find it in the policies I have."


class HelpdeskSkill:
    """Implements `SkillPort`."""

    name = "helpdesk"

    def __init__(
        self,
        router: ModelClient,
        retriever: Retriever | None = None,
        manifest: SkillManifest | None = None,
    ) -> None:
        """Bind the router and a retriever. They default to the shipped labour rules and manifest."""
        self.manifest = manifest or load_manifest(self.name)
        self.allowed_actions = list(self.manifest.allowed_actions)
        self._router = router
        self._retriever = retriever or default_retriever()
        self._prompt = load_prompt(self.manifest)

    def _output(
        self,
        answer: str,
        citations: list[dict[str, str]],
        confidence: float,
        model_id: str | None = None,
        prompt_version: str | None = None,
    ) -> SkillOutput:
        return SkillOutput(
            output={"answer": answer, "citations": citations, "confidence": confidence},
            confidence=confidence,
            skill_version=str(self.manifest.version),
            prompt_version=prompt_version,
            model_id=model_id,
        )

    @staticmethod
    def _cite(hit: Hit) -> dict[str, str]:
        return {"id": hit.chunk.id, "title": hit.chunk.title, "source": hit.chunk.source}

    def run(self, inputs: Mapping[str, Any], *, run_id: Any, step_run_id: Any) -> SkillOutput:
        """Answer `inputs["question"]`."""
        question = str(inputs["question"])
        floor = self.manifest.threshold if self.manifest.threshold is not None else DEFAULT_FLOOR
        hits = [hit for hit in self._retriever.search(question) if hit.score >= floor]
        if not hits:
            return self._output(ROUTE_TO_HR, [], 0.0)
        top = hits[0]
        passages = "\n\n".join(f"[{hit.chunk.id}] {hit.chunk.text}" for hit in hits)
        allowed = {hit.chunk.id: hit for hit in hits}

        def grounded(output: dict[str, Any]) -> float:
            cited = output.get("citations") or []
            return 1.0 if cited and all(c in allowed for c in cited) else 0.0

        try:
            result = self._router.complete(
                ModelRequest(
                    skill=self.name,
                    skill_version=str(self.manifest.version),
                    prompt_version=self.manifest.prompt_version,
                    messages=[
                        ChatMessage(role="system", content=self._prompt),
                        ChatMessage(
                            role="user", content=f"Passages:\n{passages}\n\nQuestion: {question}"
                        ),
                    ],
                    output_schema=self.manifest.output_schema,
                    run_id=run_id,
                    step_run_id=step_run_id,
                    confidence=grounded,
                    threshold=1.0,  # an answer citing anything but the passages is not accepted
                    max_output_tokens=self.manifest.max_output_tokens,
                )
            )
        except (LowConfidence, ModelUnavailable):
            result = None
        if result is not None and not self._router.is_offline(result.tier):
            citations = [self._cite(allowed[c]) for c in result.output["citations"]]
            return self._output(
                str(result.output["answer"]),
                citations,
                top.score,
                result.model_id,
                result.prompt_version,
            )
        return self._output(
            top.chunk.text,
            [self._cite(top)],
            top.score,
            result.model_id if result else None,
            result.prompt_version if result else None,
        )
