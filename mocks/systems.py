"""Declarative table of the mock systems: paths, payloads, ops and idempotency modes (PRD 15.2).

When TBO's starter kit arrives, its mocks replace these one system at a time. Only this table and
`definitions/connectors/*.yaml` know the shapes.
"""

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from mocks.store import IdempotencyMode


class _Create(BaseModel):
    """Every create carries our `client_reference`, so reconcile can find it later (PRD 11.5)."""

    model_config = ConfigDict(extra="forbid")

    client_reference: str = Field(min_length=1)


class CreateAccount(_Create):
    """directory.create_account"""

    employee_code: str
    display_name: str
    email: str


class AllocateLaptop(_Create):
    """assets.allocate_laptop"""

    employee_code: str
    sku: str


class IssueCard(_Create):
    """access.issue_card"""

    employee_code: str
    photo_ref: str


class SetupEmployee(_Create):
    """payroll.setup_employee. The CTC is stored as given: Relay never modifies compensation."""

    employee_code: str
    ctc_inr: int = Field(gt=0)


class CreateTicket(_Create):
    """ticketing.create_ticket"""

    queue: str
    subject: str
    employee_code: str | None = None


class RegisterPf(_Create):
    """statutory.register_pf (our extension)."""

    employee_code: str


class CalendarEvent(BaseModel):
    """One calendar entry."""

    title: str
    start: str
    end: str


class CreateEvents(_Create):
    """calendar.create_events (our extension)."""

    employee_code: str
    events: list[CalendarEvent] = Field(min_length=1)


class SendMessage(_Create):
    """notify.slack_dm and notify.email. Stands in for Slack and SMTP so demos run offline."""

    channel: Literal["slack", "email"]
    recipient: str
    body: str


class AddComment(BaseModel):
    """ticketing.comment"""

    model_config = ConfigDict(extra="forbid")

    comment: str = Field(min_length=1)


class NoBody(BaseModel):
    """An action that takes no fields."""

    model_config = ConfigDict(extra="forbid")


@dataclass(frozen=True, slots=True)
class ActionSpec:
    """A POST on an existing record: sets a status or appends a body field to a list."""

    op: str
    path: str
    model: type[BaseModel] = NoBody
    status: str | None = None
    append: tuple[str, str] | None = None  # (record list field, request body field)


@dataclass(frozen=True, slots=True)
class SystemSpec:
    """One mock system with a single resource collection."""

    name: str
    collection: str
    id_prefix: str
    mode: IdempotencyMode
    create_op: str
    create_model: type[_Create]
    lookup_op: str
    read_op: str
    natural_key: str | None = None
    actions: tuple[ActionSpec, ...] = field(default_factory=tuple)
    tbo_provided: bool = True

    @property
    def ops(self) -> set[str]:
        """Return every op name this system serves, including its health check."""
        return {self.create_op, self.lookup_op, self.read_op, "health"} | {
            a.op for a in self.actions
        }


SYSTEMS: tuple[SystemSpec, ...] = (
    SystemSpec(
        name="directory",
        collection="accounts",
        id_prefix="ACC",
        mode=IdempotencyMode.HEADER,
        create_op="create_account",
        create_model=CreateAccount,
        lookup_op="lookup",
        read_op="get_account",
        actions=(ActionSpec("disable_account", "disable", status="disabled"),),
    ),
    SystemSpec(
        name="assets",
        collection="laptops",
        id_prefix="LAP",
        mode=IdempotencyMode.HEADER,
        create_op="allocate_laptop",
        create_model=AllocateLaptop,
        lookup_op="lookup_by_reference",
        read_op="get_laptop",
        actions=(ActionSpec("recover_laptop", "recover", status="recovered"),),
    ),
    SystemSpec(
        name="access",
        collection="cards",
        id_prefix="CRD",
        mode=IdempotencyMode.REFERENCE_LOOKUP,
        create_op="issue_card",
        create_model=IssueCard,
        lookup_op="lookup",
        read_op="get_card",
        actions=(ActionSpec("revoke_card", "revoke", status="revoked"),),
    ),
    SystemSpec(
        name="payroll",
        collection="employees",
        id_prefix="PAY",
        mode=IdempotencyMode.NATURAL_KEY,
        natural_key="employee_code",
        create_op="setup_employee",
        create_model=SetupEmployee,
        lookup_op="lookup",
        read_op="get_employee",
        actions=(ActionSpec("notify_exit", "exit", status="exiting"),),
    ),
    SystemSpec(
        name="ticketing",
        collection="tickets",
        id_prefix="TCK",
        mode=IdempotencyMode.HEADER,
        create_op="create_ticket",
        create_model=CreateTicket,
        lookup_op="lookup",
        read_op="get_ticket",
        actions=(ActionSpec("comment", "comments", AddComment, append=("comments", "comment")),),
    ),
    SystemSpec(
        name="statutory",
        collection="pf",
        id_prefix="UANREG",
        mode=IdempotencyMode.NATURAL_KEY,
        natural_key="employee_code",
        create_op="register_pf",
        create_model=RegisterPf,
        lookup_op="lookup",
        read_op="get_registration",
        tbo_provided=False,
    ),
    SystemSpec(
        name="calendar",
        collection="events",
        id_prefix="EVT",
        mode=IdempotencyMode.REFERENCE_LOOKUP,
        create_op="create_events",
        create_model=CreateEvents,
        lookup_op="lookup",
        read_op="get_events",
        tbo_provided=False,
    ),
    SystemSpec(
        name="notify",
        collection="messages",
        id_prefix="MSG",
        mode=IdempotencyMode.HEADER,
        create_op="send",
        create_model=SendMessage,
        lookup_op="lookup",
        read_op="get_message",
        tbo_provided=False,
    ),
)
