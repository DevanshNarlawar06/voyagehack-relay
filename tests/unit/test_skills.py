"""The five skills: deterministic paths, AI-off behaviour, grounding, and what may be stored."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from relay.config import ModelsConfig
from relay.core.errors import RelayStepError
from relay.db.crypto import encrypt
from relay.models import router as router_module
from relay.models.recognizers import Entity, known_value_recognizer, verhoeff_check_digit
from relay.models.router import ModelRouter
from relay.models.types import InMemorySink, ProviderRequest, ProviderResponse
from relay.skills import build_skills
from relay.skills.base import load_manifest, load_prompt
from relay.skills.comms_drafter import CommsDrafterSkill
from relay.skills.document_extractor import (
    DocumentExtractorSkill,
    field_confidence,
    open_sealed,
    parse_fields,
    validate_fields,
)
from relay.skills.exception_analyst import ExceptionAnalystSkill, template_packet
from relay.skills.helpdesk import ROUTE_TO_HR, HelpdeskSkill
from relay.skills.intake import UNKNOWN, IntakeSkill, match_keyword
from relay.skills.retrieval import Chunk, LexicalRetriever, default_retriever

NAMES = ["intake", "document_extractor", "exception_analyst", "comms_drafter", "helpdesk"]
AADHAAR = "23456789012" + verhoeff_check_digit("23456789012")
PAN_DOC = "INCOME TAX DEPARTMENT\nName: Asha Rao\nDOB: 14/03/1995\nPAN: ABCPK1234L\n"
AADHAAR_DOC = f"Government of India\nName: Asha Rao\nDOB: 14/03/1995\n{AADHAAR}\n"
BANK_DOC = "Cancelled cheque\nA/c No: 50100123456789\nIFSC: HDFC0001234\n"
BLURRY_PAN = "INCOME TAX DEPARTMENT\nName: Asha Rao\nPAN: ABCP?1234L\n"


def mock_config() -> ModelsConfig:
    return ModelsConfig.model_validate(
        {
            "providers": {
                "mock": {"litellm_model": "mock/x", "price_inr": {"in_per_1k": 1, "out_per_1k": 1}},
            },
            "tiers": {"tier1": "mock", "tier2": "mock"},
            "defaults": {"max_output_tokens": 400, "temperature": 0, "json_mode": True},
        }
    )


def real_config() -> ModelsConfig:
    return ModelsConfig.model_validate(
        {
            "providers": {
                "real": {"litellm_model": "acme/x", "price_inr": {"in_per_1k": 1, "out_per_1k": 1}},
            },
            "tiers": {"tier1": "real", "tier2": "real"},
            "defaults": {"max_output_tokens": 400, "temperature": 0, "json_mode": True},
        }
    )


class Scripted:
    def __init__(self, replies: list[Any]) -> None:
        self.replies = replies
        self.requests: list[ProviderRequest] = []

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        self.requests.append(request)
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(reply, Exception):
            raise reply
        return ProviderResponse(json.dumps(reply) if isinstance(reply, dict) else reply, 10, 5)


def offline() -> tuple[ModelRouter, InMemorySink]:
    sink = InMemorySink()
    return ModelRouter(mock_config(), sink), sink


def online(
    monkeypatch: pytest.MonkeyPatch, *replies: Any
) -> tuple[ModelRouter, Scripted, InMemorySink]:
    provider = Scripted(list(replies))
    monkeypatch.setattr(router_module, "_select_provider", lambda _config: provider)
    sink = InMemorySink()
    return ModelRouter(real_config(), sink), provider, sink


def run(skill: Any, **inputs: Any) -> Any:
    return skill.run(inputs, run_id=None, step_run_id=None)


# ---------------------------------------------------------------- manifests


@pytest.mark.parametrize("name", NAMES)
def test_manifest_and_prompt_load(name: str) -> None:
    manifest = load_manifest(name)
    assert manifest.allowed_actions
    assert load_prompt(manifest).strip()


def test_a_manifest_must_match_its_file_name(tmp_path: Path) -> None:
    text = (Path("definitions/skills/intake.yaml")).read_text(encoding="utf-8")
    (tmp_path / "other.yaml").write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match="declares skill"):
        load_manifest("other", tmp_path)


def test_build_skills_gives_the_engine_all_five() -> None:
    router, _ = offline()
    assert set(build_skills(router)) == set(NAMES)


@pytest.mark.parametrize("name", NAMES)
def test_prompts_hold_no_rule_values(name: str) -> None:
    """PRD 6.8: statutory numbers come from rules, never from prompts."""
    prompt = load_prompt(load_manifest(name))
    for value in ("15000", "150000", "12%", "18 days", "2000000", "6 months"):
        assert value not in prompt


# ---------------------------------------------------------------- intake


@pytest.mark.parametrize(
    ("text", "intent"),
    [
        ("what's my onboarding status", "onboarding_status"),
        ("I've uploaded my documents", "documents_uploaded"),
        ("how many leaves do I get in probation", "policy_question"),
        ("I want to resign", "resign"),
        ("show me my data", "data_request"),
        ("I need to report sexual harassment by my lead", "posh_report"),
        ("I want to file a formal complaint", "grievance_report"),
        ("I want to report fraud in procurement", "whistleblower_report"),
    ],
)
def test_keyword_table(text: str, intent: str) -> None:
    router, sink = offline()
    result = run(IntakeSkill(router), text=text)
    assert result.output["intent"] == intent
    assert result.confidence == 1.0
    assert sink.payloads == []  # no model call, no cost


def test_sensitive_matters_win_over_anything_else_in_the_message() -> None:
    assert match_keyword("I want to resign because of sexual harassment") == "posh_report"


def test_unclear_message_offline_is_unknown_not_mock_noise() -> None:
    router, sink = offline()
    result = run(IntakeSkill(router), text="the coffee machine is broken again")
    assert result.output["intent"] == UNKNOWN
    assert result.confidence == 0.0
    assert sink.costs  # the path was still exercised


def test_model_classifies_a_miss_when_samples_agree(monkeypatch: pytest.MonkeyPatch) -> None:
    router, provider, _ = online(monkeypatch, {"intent": "policy_question"})
    result = run(IntakeSkill(router), text="am I allowed some time off soon")
    assert result.output["intent"] == "policy_question"
    assert result.confidence == 1.0
    assert result.model_id == "acme/x"
    assert len(provider.requests) == 3


def test_model_disagreement_is_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    replies = [{"intent": i} for i in ("resign", "unknown", "policy_question")] * 2
    router, _, _ = online(monkeypatch, *replies)
    assert run(IntakeSkill(router), text="hmm").output["intent"] == UNKNOWN


def test_provider_outage_is_unknown_not_a_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    router, _, _ = online(monkeypatch, RuntimeError("503"))
    assert run(IntakeSkill(router), text="hmm").output["intent"] == UNKNOWN


# ---------------------------------------------------------------- document extractor


def test_clean_documents_need_no_model() -> None:
    router, sink = offline()
    result = run(
        DocumentExtractorSkill(router),
        documents=[
            {"category": "pan", "text": PAN_DOC},
            {"category": "aadhaar", "text": AADHAAR_DOC},
            {"category": "bank_proof", "text": BANK_DOC},
        ],
    )
    assert result.confidence == 1.0
    assert sink.payloads == []
    assert result.output["validation"]["pan"] == {"pan": True, "name": True, "dob": True}


def test_raw_identifiers_never_reach_the_stored_output() -> None:
    """Step output is unencrypted JSONB, so only masked forms and a sealed blob may be in it."""
    router, _ = offline()
    result = run(
        DocumentExtractorSkill(router),
        documents=[
            {"category": "pan", "text": PAN_DOC},
            {"category": "aadhaar", "text": AADHAAR_DOC},
            {"category": "bank_proof", "text": BANK_DOC},
        ],
    )
    stored = json.dumps(result.output)
    for raw in ("ABCPK1234L", AADHAAR, "50100123456789", "14/03/1995"):
        assert raw not in stored
    assert result.output["fields"]["pan"]["pan"].endswith("234L")
    assert result.output["fields"]["pan"]["name"] == "Asha Rao"
    opened = open_sealed(result.output["sealed"]["pan"])
    assert opened == {"pan": "ABCPK1234L", "name": "Asha Rao", "dob": "14/03/1995"}


def test_a_blurry_document_scores_low_for_the_confidence_gate() -> None:
    router, sink = offline()
    result = run(
        DocumentExtractorSkill(router), documents=[{"category": "pan", "text": BLURRY_PAN}]
    )
    assert result.confidence == pytest.approx(2 / 6)
    assert result.output["validation"]["pan"]["pan"] is False
    assert sink.costs
    assert "ABCP" not in json.dumps(result.output["fields"])


def test_overall_confidence_is_the_weakest_document() -> None:
    router, _ = offline()
    result = run(
        DocumentExtractorSkill(router),
        documents=[{"category": "pan", "text": PAN_DOC}, {"category": "pan", "text": BLURRY_PAN}],
    )
    assert result.confidence == pytest.approx(2 / 6)


def test_no_documents_is_zero_confidence() -> None:
    router, _ = offline()
    assert run(DocumentExtractorSkill(router), documents=[]).confidence == 0.0


def test_only_masked_text_goes_to_a_model_and_it_can_fill_a_gap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    doc = "Name: Asha Rao\nDOB: 14/03/1995\nPermanent no. ABCPK1234L smudged"
    router, provider, _ = online(monkeypatch, {"pan": "<PAN_1>"})
    # An Aadhaar card with no Aadhaar number: the parser misses it, so the model is asked.
    result = run(DocumentExtractorSkill(router), documents=[{"category": "aadhaar", "text": doc}])
    sent = json.dumps(provider.requests[0].messages)
    assert "ABCPK1234L" not in sent
    assert "<PAN_1>" in sent
    assert result.output["validation"]["aadhaar"]["aadhaar"] is False


def test_a_model_answer_failing_validators_is_not_believed(monkeypatch: pytest.MonkeyPatch) -> None:
    router, _, _ = online(monkeypatch, {"pan": "NOTAPAN", "dob": "99/99/9999"})
    result = run(
        DocumentExtractorSkill(router), documents=[{"category": "pan", "text": BLURRY_PAN}]
    )
    assert result.output["validation"]["pan"]["pan"] is False
    assert "pan" not in result.output["fields"]["pan"]


@pytest.mark.parametrize(
    ("field", "good", "bad"),
    [
        ("pan", "ABCPK1234L", "ABCPK12345"),
        ("aadhaar", AADHAAR, "234567890123"),
        ("ifsc", "HDFC0001234", "HDFC1001234"),
        ("account", "50100123456789", "12ab"),
        ("name", "Asha Rao", "A"),
        ("dob", "14/03/1995", "14/03/2030"),
    ],
)
def test_validators(field: str, good: str, bad: str) -> None:
    category = {
        "pan": "pan",
        "aadhaar": "aadhaar",
        "ifsc": "bank_proof",
        "account": "bank_proof",
        "name": "pan",
        "dob": "pan",
    }[field]
    assert validate_fields(category, {field: good})[field] is True
    assert validate_fields(category, {field: bad})[field] is False


def test_unknown_category_gets_nothing() -> None:
    assert parse_fields("education", PAN_DOC) == {}
    assert field_confidence("education", {}) == 0.0


def test_sealed_blob_needs_the_key() -> None:
    assert open_sealed(encrypt(json.dumps({"a": "b"}))) == {"a": "b"}


# ---------------------------------------------------------------- exception analyst


INPUTS = {
    "kind": "conflict",
    "step": "cross_check",
    "reason": "Name on PAN differs",
    "fields": ["full_name"],
    "options": ["retry", "override", "fail"],
    "facts": {"offer": "Asha Rao"},
}


def test_template_uses_only_facts() -> None:
    packet = template_packet(INPUTS)
    assert "Name on PAN differs" in packet["summary"]
    assert "full_name" in packet["summary"]
    assert packet["suggested_option"] is None


def test_offline_analyst_returns_the_template_and_records_the_call() -> None:
    router, sink = offline()
    result = run(ExceptionAnalystSkill(router), **INPUTS)
    assert result.output == template_packet(INPUTS)
    assert sink.costs


def test_model_can_suggest_only_a_real_option(monkeypatch: pytest.MonkeyPatch) -> None:
    reply = {"summary": "s", "likely_cause": "c", "suggested_option": "approve_everything"}
    router, _, _ = online(monkeypatch, reply)
    assert run(ExceptionAnalystSkill(router), **INPUTS).output["suggested_option"] is None
    router, _, _ = online(monkeypatch, {**reply, "suggested_option": "override"})
    assert run(ExceptionAnalystSkill(router), **INPUTS).output["suggested_option"] == "override"


def test_analyst_survives_an_outage(monkeypatch: pytest.MonkeyPatch) -> None:
    router, _, _ = online(monkeypatch, RuntimeError("503"))
    assert run(ExceptionAnalystSkill(router), **INPUTS).output == template_packet(INPUTS)


# ---------------------------------------------------------------- comms drafter


FACTS = {
    "joiner_name": "Asha Rao",
    "joining_date": "4 Jan 2027",
    "role_title": "Engineer",
    "buddy_name": "Vikram Sen",
}


def test_offline_comms_is_the_template_not_mock_noise() -> None:
    router, _ = offline()
    result = run(CommsDrafterSkill(router), template="manager_brief", facts=FACTS)
    assert result.output["subject"] == "Asha Rao joins on 4 Jan 2027"
    assert "Vikram Sen" in result.output["body"]
    assert "mock" not in result.output["body"]


def test_a_missing_fact_is_visible_not_a_crash() -> None:
    router, _ = offline()
    result = run(CommsDrafterSkill(router), template="welcome", facts={"joiner_name": "Asha Rao"})
    assert "[joining_date not available]" in result.output["body"]


def test_unknown_template_is_refused() -> None:
    router, _ = offline()
    with pytest.raises(ValueError, match="unknown template"):
        run(CommsDrafterSkill(router), template="nope", facts={})


def test_names_are_masked_for_the_model_and_restored_after(monkeypatch: pytest.MonkeyPatch) -> None:
    reply = {"subject": "Hello <PERSON_1>", "body": "Welcome <PERSON_1> and <PERSON_2>"}
    router, provider, sink = online(monkeypatch, reply)
    result = run(CommsDrafterSkill(router), template="manager_brief", facts=FACTS)
    sent = json.dumps(provider.requests[0].messages)
    assert "Asha Rao" not in sent
    assert "Vikram Sen" not in sent
    assert "Asha Rao" in result.output["subject"]
    stored = " ".join(row.masked_text for row in sink.payloads)
    assert "Asha Rao" not in stored
    assert "Vikram Sen" not in stored


def test_comms_output_is_capped_at_150_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    router, provider, _ = online(monkeypatch, {"subject": "s", "body": "b"})
    run(CommsDrafterSkill(router), template="welcome", facts=FACTS)
    assert provider.requests[0].max_output_tokens == 150


def test_known_value_recognizer_is_exact_and_case_insensitive() -> None:
    recognizer = known_value_recognizer(Entity.PERSON, ["Asha Rao", " "])
    assert recognizer is not None
    assert [s.text for s in recognizer.analyze("asha rao met Ashaan Rao")] == ["asha rao"]
    assert known_value_recognizer(Entity.PERSON, [""]) is None


# ---------------------------------------------------------------- helpdesk and retrieval


def test_questions_find_the_right_policy() -> None:
    retriever = default_retriever()
    assert (
        retriever.search("how many leaves do I get in probation")[0].chunk.id == "leave.probation"
    )
    assert retriever.search("what is my notice period if I resign")[0].chunk.id == "notice.period"
    assert retriever.search("when is gratuity payable")[0].chunk.id == "gratuity.eligibility"
    assert retriever.search("what is my PF contribution")[0].chunk.id == "pf.contribution"


def test_every_chunk_cites_a_source() -> None:
    retriever = default_retriever()
    assert all(
        hit.chunk.source for hit in retriever.search("leave notice gratuity probation", limit=20)
    )


def test_answers_follow_the_rules_file(tmp_path: Path) -> None:
    """PRD 6.8: change the table, and the answer changes, with no prompt edit."""
    source = yaml.safe_load(
        Path("definitions/policies/labour_rules.yaml").read_text(encoding="utf-8")
    )
    source["tables"]["gratuity"]["min_continuous_service_years"] = 7
    path = tmp_path / "labour_rules.yaml"
    path.write_text(yaml.safe_dump(source), encoding="utf-8")
    hit = default_retriever(path).search("when is gratuity payable")[0]
    assert "after 7 years" in hit.chunk.text


def test_empty_and_stopword_queries_find_nothing() -> None:
    assert default_retriever().search("") == []
    assert default_retriever().search("what is the") == []


def test_offline_helpdesk_quotes_the_passage_with_a_citation() -> None:
    router, sink = offline()
    result = run(HelpdeskSkill(router), question="how many leaves do I get in probation")
    assert result.output["citations"][0]["id"] == "leave.probation"
    assert "earned leave" in result.output["answer"]
    assert sink.costs


@pytest.mark.parametrize(
    "question",
    ["what is the wifi password", "can I bring my dog to the office", "who won the cricket match"],
)
def test_off_topic_questions_go_to_hr(question: str) -> None:
    router, sink = offline()
    result = run(HelpdeskSkill(router), question=question)
    assert result.output == {"answer": ROUTE_TO_HR, "citations": [], "confidence": 0.0}
    assert sink.payloads == []


def test_model_answer_with_real_citations_is_used(monkeypatch: pytest.MonkeyPatch) -> None:
    reply = {"answer": "You cannot use earned leave.", "citations": ["leave.probation"]}
    router, provider, _ = online(monkeypatch, reply)
    result = run(HelpdeskSkill(router), question="how many leaves do I get in probation")
    assert result.output["answer"] == "You cannot use earned leave."
    assert result.output["citations"][0]["title"] == "Leave during probation"
    assert result.model_id == "acme/x"
    assert "leave.probation" in json.dumps(provider.requests[0].messages)


def test_model_citing_something_it_was_not_given_falls_back_to_the_passage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reply = {"answer": "You get 90 days.", "citations": ["made.up"]}
    router, _, _ = online(monkeypatch, reply)
    result = run(HelpdeskSkill(router), question="how many leaves do I get in probation")
    assert "90 days" not in result.output["answer"]
    assert result.output["citations"][0]["id"] == "leave.probation"


def test_helpdesk_survives_an_outage(monkeypatch: pytest.MonkeyPatch) -> None:
    router, _, _ = online(monkeypatch, RuntimeError("503"))
    result = run(HelpdeskSkill(router), question="when is gratuity payable")
    assert result.output["citations"][0]["id"] == "gratuity.eligibility"


def test_retriever_ignores_an_empty_corpus() -> None:
    assert LexicalRetriever([]).search("leave") == []


def test_a_custom_retriever_can_be_plugged_in() -> None:
    chunk = Chunk("x", "Parking", "Parking is free for all staff.", "Facilities §1")
    router, _ = offline()
    result = run(HelpdeskSkill(router, LexicalRetriever([chunk])), question="is parking free")
    assert result.output["citations"][0]["source"] == "Facilities §1"


# ---------------------------------------------------------------- engine fit


def test_only_masked_values_are_sealed_or_shown_after_a_router_swap() -> None:
    router, _ = offline()
    assert build_skills(router)["helpdesk"].allowed_actions == ["chat.answer"]


def test_skills_never_import_connectors_or_http() -> None:
    for path in Path("relay/skills").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "relay.connectors" not in text, path
        assert "httpx" not in text, path


def test_skill_failures_stay_in_the_taxonomy(monkeypatch: pytest.MonkeyPatch) -> None:
    """An outage never escapes a skill as an unclassified error: it degrades instead."""
    router, _, _ = online(monkeypatch, RuntimeError("503"))
    for skill, inputs in (
        (IntakeSkill(router), {"text": "hmm"}),
        (DocumentExtractorSkill(router), {"documents": [{"category": "pan", "text": BLURRY_PAN}]}),
        (ExceptionAnalystSkill(router), INPUTS),
        (CommsDrafterSkill(router), {"template": "welcome", "facts": FACTS}),
        (HelpdeskSkill(router), {"question": "when is gratuity payable"}),
    ):
        try:
            skill.run(inputs, run_id=None, step_run_id=None)
        except RelayStepError:
            pytest.fail(f"{skill.name} should degrade rather than raise")


def test_seed_style_sidecars_are_understood() -> None:
    """The shape in seed/fixtures/candidates.json: structured fields, ISO dates, `full_name`."""
    router, sink = offline()
    sidecar = {
        "full_name": "Asha Rao",
        "dob": "1995-03-14",
        "pan": "ABCPK1234L",
        "bank_account": "50100123456789",
        "ifsc": "HDFC0001234",
    }
    result = run(
        DocumentExtractorSkill(router),
        documents=[
            {"category": "pan", "fields": sidecar},
            {"category": "bank_proof", "fields": sidecar},
        ],
    )
    assert result.confidence == 1.0
    assert sink.payloads == []
    assert open_sealed(result.output["sealed"]["bank_proof"])["account"] == "50100123456789"
    assert "ABCPK1234L" not in json.dumps(result.output["fields"])


def test_a_doubtful_reader_caps_confidence_even_when_validators_pass() -> None:
    """A misread can still look valid, so a blurry card must reach a person."""
    router, _ = offline()
    result = run(
        DocumentExtractorSkill(router),
        documents=[{"category": "pan", "text": PAN_DOC, "ocr_confidence": 0.41}],
    )
    assert all(result.output["validation"]["pan"].values())
    assert result.confidence == pytest.approx(0.41)
