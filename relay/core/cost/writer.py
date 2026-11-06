"""Cost events (PRD 10.3, 17). Costs are measured per run, never estimated."""

from __future__ import annotations

import uuid
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from relay.config import InfraCostsConfig, Price, VolumesConfig
from relay.db.models import CostEvent, CostKind

_SECONDS_PER_MONTH = 30 * 24 * 3600
_FOUR_PLACES = Decimal("0.0001")


def round_inr(value: Decimal) -> Decimal:
    """Round to the precision of `cost_events.cost_inr`, numeric(12,4)."""
    return value.quantize(_FOUR_PLACES, rounding=ROUND_HALF_UP)


def llm_cost_inr(price: Price, tokens_in: int, tokens_out: int) -> Decimal:
    """Return tokens_in·p_in + tokens_out·p_out, with prices per 1000 tokens."""
    # str() keeps the decimal the config author wrote, rather than the float's binary expansion.
    total = Decimal(tokens_in) * Decimal(str(price.in_per_1k)) + Decimal(tokens_out) * Decimal(
        str(price.out_per_1k)
    )
    return round_inr(total / 1000)


def record_llm_cost(
    session: Session,
    *,
    run_id: uuid.UUID | None,
    step_run_id: uuid.UUID | None,
    provider: str,
    model_id: str,
    price: Price,
    tokens_in: int,
    tokens_out: int,
) -> CostEvent:
    """Write one `llm` cost event for one model call."""
    event = CostEvent(
        run_id=run_id,
        step_run_id=step_run_id,
        kind=CostKind.LLM,
        provider=provider,
        model_id=model_id,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        unit_prices={"in_per_1k": str(price.in_per_1k), "out_per_1k": str(price.out_per_1k)},
        cost_inr=llm_cost_inr(price, tokens_in, tokens_out),
    )
    session.add(event)
    session.flush()
    return event


def cpu_price_per_second(infra: InfraCostsConfig) -> Decimal:
    """Return p_cpu = VM monthly cost ÷ vCPU-seconds per month (PRD 17)."""
    vm = Decimal(str(infra.monthly.get("vm", 0)))
    return vm / (infra.vm_vcpus * _SECONDS_PER_MONTH)


def record_cpu_cost(
    session: Session,
    *,
    run_id: uuid.UUID | None,
    step_run_id: uuid.UUID | None,
    kind: Literal[CostKind.OCR_CPU, CostKind.EMBEDDING_CPU],
    cpu_seconds: float,
    infra: InfraCostsConfig,
) -> CostEvent:
    """Write one local-compute cost event, for OCR or embeddings."""
    seconds = Decimal(str(cpu_seconds))
    price = cpu_price_per_second(infra)
    event = CostEvent(
        run_id=run_id,
        step_run_id=step_run_id,
        kind=kind,
        cpu_seconds=round_inr(seconds),
        unit_prices={"inr_per_cpu_second": str(price)},
        cost_inr=round_inr(seconds * price),
    )
    session.add(event)
    session.flush()
    return event


def projected_monthly_runs(volumes: VolumesConfig) -> int | None:
    """Return the sum of known monthly volumes, or None if TBO has shared none yet."""
    known = [count for count in volumes.monthly.values() if count]
    return sum(known) if known else None


def allocate_infra(
    session: Session, run_id: uuid.UUID, infra: InfraCostsConfig, volumes: VolumesConfig
) -> bool:
    """Charge a completed run its share: monthly_fixed_inr ÷ projected_monthly_runs (PRD 17).

    Safe to call again after a crash: a unique index allows one allocation per run. Returns whether
    this call wrote the row. With no known volumes the share is recorded as 0 and says so in
    `unit_prices`, rather than being guessed.
    """
    runs = projected_monthly_runs(volumes)
    fixed = Decimal(str(infra.monthly_fixed_inr))
    share = round_inr(fixed / runs) if runs else Decimal(0)
    statement = (
        insert(CostEvent)
        .values(
            run_id=run_id,
            kind=CostKind.INFRA_ALLOC,
            unit_prices={"monthly_fixed_inr": str(fixed), "projected_monthly_runs": runs},
            cost_inr=share,
        )
        .on_conflict_do_nothing(
            index_elements=[CostEvent.run_id],
            index_where=text("kind = 'infra_alloc'"),
        )
        .returning(CostEvent.id)
    )
    return session.execute(statement).first() is not None
