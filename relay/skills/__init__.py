"""The five V1 skills (PRD 13.2). They return outputs; the engine decides what happens next."""

from __future__ import annotations

from relay.core.engine.ports import SkillPort
from relay.models.types import ModelClient
from relay.skills.comms_drafter import CommsDrafterSkill
from relay.skills.document_extractor import DocumentExtractorSkill
from relay.skills.exception_analyst import ExceptionAnalystSkill
from relay.skills.helpdesk import HelpdeskSkill
from relay.skills.intake import IntakeSkill


def build_skills(router: ModelClient) -> dict[str, SkillPort]:
    """Build every skill for `EngineDeps.skills`, keyed by the name workflows use."""
    skills: list[SkillPort] = [
        IntakeSkill(router),
        DocumentExtractorSkill(router),
        ExceptionAnalystSkill(router),
        CommsDrafterSkill(router),
        HelpdeskSkill(router),
    ]
    return {skill.name: skill for skill in skills}
