"""Registry validation (PRD 11.1): a definition can only be activated if this returns no errors.

Checks: schema; unique step ids; every `needs` resolves; no cycles; every CEL expression
compiles; every `uses` resolves to a registered rule, connector op or skill; every `action` is
declared in rbac.yaml; a human step for a reserved action is assigned to a role that can decide it.
"""

from __future__ import annotations

from collections.abc import Iterable

from relay.core.dsl.model import Step, WorkflowDefinition
from relay.core.engine.ports import EngineDeps
from relay.core.policy import cel
from relay.core.policy.packs import PolicySet


def _cel_exprs(step: Step) -> Iterable[tuple[str, str]]:
    if step.when:
        yield "when", step.when
    if step.until:
        yield "until", step.until
    for group in ("inputs", "attributes", "outputs"):
        for key, expr in getattr(step, group).items():
            yield f"{group}.{key}", expr
    if step.task:
        if step.task.assignee_user:
            yield "task.assignee_user", step.task.assignee_user
        for i, expr in enumerate(step.task.context_from):
            yield f"task.context_from[{i}]", expr


def _cycle(steps: list[Step]) -> list[str] | None:
    graph = {s.id: s.needs for s in steps}
    state: dict[str, int] = {}
    stack: list[str] = []

    def visit(node: str) -> list[str] | None:
        state[node] = 1
        stack.append(node)
        for nxt in graph.get(node, []):
            if state.get(nxt) == 1:
                return [*stack[stack.index(nxt) :], nxt]
            if state.get(nxt) is None:
                found = visit(nxt)
                if found:
                    return found
        stack.pop()
        state[node] = 2
        return None

    for node in graph:
        if state.get(node) is None:
            found = visit(node)
            if found:
                return found
    return None


def validate_definition(
    wf: WorkflowDefinition, policies: PolicySet, deps: EngineDeps | None = None
) -> list[str]:
    """Return human-readable errors; empty means valid. `deps=None` skips `uses` resolution."""
    errors: list[str] = []
    steps = wf.flat_steps()
    ids = [s.id for s in steps]
    dupes = {i for i in ids if ids.count(i) > 1}
    errors += [f"duplicate step id {d!r}" for d in sorted(dupes)]
    known = set(ids)
    declared = set(policies.rbac.actions)

    for expr_name, expr in wf.context.items():
        try:
            cel.check(expr)
        except cel.CelError as exc:
            errors.append(f"context.{expr_name}: {exc}")
    for i, child in enumerate(wf.on_complete):
        for key, expr in child.payload.items():
            try:
                cel.check(expr)
            except cel.CelError as exc:
                errors.append(f"on_complete[{i}].payload.{key}: {exc}")

    for s in steps:
        for n in s.needs:
            if n not in known:
                errors.append(f"{s.id}: needs unknown step {n!r}")
        for where, expr in _cel_exprs(s):
            try:
                cel.check(expr)
            except cel.CelError as exc:
                errors.append(f"{s.id}.{where}: {exc}")
        if s.action not in declared:
            errors.append(f"{s.id}: action {s.action!r} is not declared in rbac.yaml actions")
        if s.kind == "human" and s.task is not None:
            approver = policies.reserved_approver(s.action)
            role = s.task.assignee_role
            if approver is not None and role != approver:
                errors.append(
                    f"{s.id}: reserved action {s.action} must be assigned to {approver}, not {role}"
                )
            if not policies.rbac.permits([role], s.action):
                errors.append(f"{s.id}: role {role!r} is not permitted to perform {s.action}")
        if deps is not None and s.uses:
            if s.kind == "rule" and s.uses not in deps.rules:
                errors.append(f"{s.id}: unknown rule {s.uses!r}")
            elif s.kind == "connector":
                try:
                    deps.connector_op(s.uses)
                except KeyError as exc:
                    errors.append(f"{s.id}: {exc.args[0]}")
            elif s.kind == "skill" and s.uses not in deps.skills:
                errors.append(f"{s.id}: unknown skill {s.uses!r}")

    cycle = _cycle(steps)
    if cycle:
        errors.append("cycle: " + " -> ".join(cycle))
    if (
        wf.trigger.require_decision
        and policies.reserved_approver(wf.trigger.require_decision) is None
    ):
        errors.append(
            f"trigger.require_decision {wf.trigger.require_decision!r} is not a reserved action"
        )
    return errors
