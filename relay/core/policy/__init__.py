"""authorize(), CEL evaluator, baseline reserved floor, pack loader, kill switches (PRD 12).

Owner: Session A, WP3. Public surface:
- `ActionRequest`, `Actor`, `SubjectRef`, `Decision`: the contract (PRD 12.1)
- `evaluate(req, ctx)`: pure decision function
- `PolicyService`: DB-backed `authorize()`, activation, rollback, kill switches
"""

from relay.core.policy.evaluate import EvaluationContext, KillSwitchState, evaluate
from relay.core.policy.service import PolicyService, PolicyTestFailure
from relay.core.policy.types import ActionRequest, Actor, Decision, SubjectRef

__all__ = [
    "ActionRequest",
    "Actor",
    "Decision",
    "EvaluationContext",
    "KillSwitchState",
    "PolicyService",
    "PolicyTestFailure",
    "SubjectRef",
    "evaluate",
]
