"""Postgres implementation of the router's sink: `model_payloads` and `cost_events` rows."""

from __future__ import annotations

from sqlalchemy.orm import Session, sessionmaker

from relay.db.models import CostEvent, CostKind, ModelPayload, PayloadDirection
from relay.models.types import CostRecord, PayloadRecord


class DatabaseSink:
    """Writes within the caller's transaction, so a rolled-back step leaves no orphan rows."""

    def __init__(self, session: Session) -> None:
        """Bind the sink to a session. The caller owns the commit."""
        self._session = session

    def record_payload(self, payload: PayloadRecord) -> None:
        """Insert one append-only `model_payloads` row, keeping the id the router linked."""
        self._session.add(
            ModelPayload(
                id=payload.id,
                run_id=payload.run_id,
                step_run_id=payload.step_run_id,
                direction=PayloadDirection(payload.direction),
                masked_text=payload.masked_text,
                provider=payload.provider,
                model_id=payload.model_id,
                region=payload.region,
                token_count=payload.token_count,
            )
        )
        self._session.flush()

    def record_cost(self, cost: CostRecord) -> None:
        """Insert one `llm` cost event."""
        self._session.add(
            CostEvent(
                run_id=cost.run_id,
                step_run_id=cost.step_run_id,
                kind=CostKind.LLM,
                provider=cost.provider,
                model_id=cost.model_id,
                tokens_in=cost.tokens_in,
                tokens_out=cost.tokens_out,
                unit_prices=cost.unit_prices,
                cost_inr=cost.cost_inr,
            )
        )
        self._session.flush()


class IndependentSink:
    """Writes each row in its own transaction, for callers with no session, such as skills.

    What left our infrastructure is on record even if the step that sent it later rolls back.
    """

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        """Bind a session factory."""
        self._sessions = sessions

    def record_payload(self, payload: PayloadRecord) -> None:
        """Insert one `model_payloads` row and commit."""
        with self._sessions.begin() as session:
            DatabaseSink(session).record_payload(payload)

    def record_cost(self, cost: CostRecord) -> None:
        """Insert one `cost_events` row and commit."""
        with self._sessions.begin() as session:
            DatabaseSink(session).record_cost(cost)
