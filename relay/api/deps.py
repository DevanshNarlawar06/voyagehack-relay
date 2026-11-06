"""Request-scoped dependencies: database session, signed-in user and `authorize()` (PRD 16.1)."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Protocol
from uuid import UUID

import jwt
from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from relay.config import RelayConfig, get_settings
from relay.connectors.base import Connector, FaultControl
from relay.core.policy import ActionRequest, Actor, Decision, PolicyService, SubjectRef
from relay.db.models import HumanTask, User
from relay.db.session import session_scope

_JWT_ALGORITHM = "HS256"
_TOKEN_LIFETIME = timedelta(hours=12)
_DEV_JWT_SECRET = "dev-only-secret"


class EngineGateway(Protocol):
    """What the API needs from the workflow engine (#4). Routers stay ignorant of how it works."""

    def start_event(
        self, session: Session, event: str, payload: dict[str, Any], actor: str
    ) -> list[UUID]:
        """Start the workflows triggered by a domain event and return their run ids."""
        ...

    def cancel_run(self, session: Session, run_id: UUID, actor: str) -> None:
        """Cancel a run."""
        ...

    def task_decided(self, session: Session, task: HumanTask) -> None:
        """Resume the step waiting on a human task that was just decided."""
        ...


@dataclass
class ApiServices:
    """Long-lived collaborators, built once at startup and held on `app.state`."""

    policy: PolicyService
    config: RelayConfig
    connectors: dict[str, Connector] = field(default_factory=dict)
    faults: FaultControl | None = None
    engine: EngineGateway | None = None


def get_services(request: Request) -> ApiServices:
    """Return the app's services."""
    services: ApiServices = request.app.state.services
    return services


def get_session() -> Iterator[Session]:
    """Yield a session that commits on success and rolls back on error."""
    with session_scope() as session:
        yield session


Services = Annotated[ApiServices, Depends(get_services)]
Db = Annotated[Session, Depends(get_session)]


def _secret(config: RelayConfig) -> str:
    secret = get_settings().relay_jwt_secret
    if secret == _DEV_JWT_SECRET and not config.app.demo_mode:
        raise RuntimeError("RELAY_JWT_SECRET must be set outside demo mode")
    # Hashing gives HS256 a full-length key whatever length of secret was configured.
    return hashlib.sha256(secret.encode()).hexdigest()


def issue_token(email: str, config: RelayConfig) -> str:
    """Sign a demo session token. Roles are read from the database on each request, not from here."""
    now = datetime.now(UTC)
    claims = {"sub": email, "iat": now, "exp": now + _TOKEN_LIFETIME}
    return jwt.encode(claims, _secret(config), algorithm=_JWT_ALGORITHM)


def get_current_user(
    session: Db,
    services: Services,
    authorization: Annotated[str | None, Header()] = None,
) -> User:
    """Resolve the bearer token to a user, or answer 401."""
    unauthorized = HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        "Sign in to continue.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if not authorization or not authorization.startswith("Bearer "):
        raise unauthorized
    try:
        claims = jwt.decode(
            authorization.removeprefix("Bearer "),
            _secret(services.config),
            algorithms=[_JWT_ALGORITHM],
        )
    except jwt.PyJWTError:
        raise unauthorized from None
    user = session.scalar(select(User).where(User.email == claims.get("sub")))
    if user is None:
        raise unauthorized
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def actor_for(user: User) -> Actor:
    """Return the policy actor for a signed-in person."""
    return Actor(type="human", id=f"human:{user.email}", roles=list(user.roles))


@dataclass
class Authorizer:
    """Every endpoint goes through this, so every request leaves a `decisions` row (PRD 16.1)."""

    session: Session
    user: User
    policy: PolicyService

    def check(
        self,
        action: str,
        *,
        subject: SubjectRef | None = None,
        attributes: dict[str, Any] | None = None,
        run_id: UUID | None = None,
        workflow: str | None = None,
        step_id: str | None = None,
    ) -> Decision:
        """Ask the policy engine, keeping the decision and audit rows whatever it answers."""
        decision, _ = self.policy.authorize(
            self.session,
            ActionRequest(
                action=action,
                actor=actor_for(self.user),
                subject=subject,
                attributes=attributes or {},
                run_id=run_id,
                workflow=workflow,
                step_id=step_id,
            ),
        )
        # Committed here because the 403 below rolls back, and a refusal must still be on record.
        self.session.commit()
        return decision

    def require(self, action: str, **kwargs: Any) -> Decision:
        """Answer 403 unless the policy engine says ALLOW."""
        decision = self.check(action, **kwargs)
        if decision.effect != "ALLOW":
            raise HTTPException(status.HTTP_403_FORBIDDEN, decision.reason)
        return decision


def get_authorizer(session: Db, user: CurrentUser, services: Services) -> Authorizer:
    """Build the authorizer for this request."""
    return Authorizer(session, user, services.policy)


Auth = Annotated[Authorizer, Depends(get_authorizer)]
