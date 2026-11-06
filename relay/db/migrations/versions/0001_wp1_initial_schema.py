"""wp1 initial schema

Revision ID: 0001
Revises:
Create Date: 2026-11-07 00:43:46.318508
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

import relay.db.crypto
from relay.db.protections import downgrade_statements, upgrade_statements

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "audit_events",
        sa.Column("seq", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column(
            "ts", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "actor_type",
            sa.Enum(
                "human",
                "agent",
                "system",
                name="actor_type",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("actor_id", sa.String(length=200), nullable=False),
        sa.Column("on_behalf_of", sa.String(length=200), nullable=True),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("subject_type", sa.String(length=32), nullable=True),
        sa.Column("subject_id", sa.String(length=64), nullable=True),
        sa.Column("run_id", sa.UUID(), nullable=True),
        sa.Column("step_run_id", sa.UUID(), nullable=True),
        sa.Column("effect", sa.String(length=32), nullable=True),
        sa.Column("workflow_def_version", sa.String(length=64), nullable=True),
        sa.Column("policy_version", sa.String(length=500), nullable=True),
        sa.Column("skill_version", sa.String(length=64), nullable=True),
        sa.Column("prompt_version", sa.String(length=64), nullable=True),
        sa.Column("model_id", sa.String(length=100), nullable=True),
        sa.Column("input_hash", sa.String(length=64), nullable=True),
        sa.Column("masked_payload_ref", sa.UUID(), nullable=True),
        sa.Column("reasoning", sa.Text(), nullable=True),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("prev_hash", sa.String(length=64), nullable=False),
        sa.Column("hash", sa.String(length=64), nullable=False),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("hash"),
        sa.UniqueConstraint("seq"),
    )
    op.create_index("ix_audit_events_run", "audit_events", ["run_id"], unique=False)
    op.create_index(
        "ix_audit_events_subject", "audit_events", ["subject_type", "subject_id"], unique=False
    )
    op.create_table(
        "decisions",
        sa.Column("request", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "effect",
            sa.Enum(
                "ALLOW",
                "REQUIRE_HUMAN",
                "DENY",
                name="effect",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("approver_role", sa.String(length=64), nullable=True),
        sa.Column("rule_ids", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("policy_version", sa.String(length=500), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "idempotency_ledger",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("connector", sa.String(length=64), nullable=False),
        sa.Column("operation", sa.String(length=64), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "succeeded",
                "failed_permanent",
                name="ledger_status",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("external_id", sa.String(length=200), nullable=True),
        sa.Column("response", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "first_attempt_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key"),
    )
    op.create_table(
        "kill_switches",
        sa.Column(
            "scope",
            sa.Enum(
                "global",
                "workflow",
                "step",
                "skill",
                "connector",
                name="kill_scope",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("target", sa.String(length=100), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("set_by", sa.String(length=200), nullable=False),
        sa.Column(
            "set_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("scope", "target"),
    )
    op.create_table(
        "model_payloads",
        sa.Column("run_id", sa.UUID(), nullable=True),
        sa.Column("step_run_id", sa.UUID(), nullable=True),
        sa.Column(
            "direction",
            sa.Enum(
                "out",
                "in",
                name="payload_direction",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("masked_text", sa.Text(), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model_id", sa.String(length=100), nullable=True),
        sa.Column("region", sa.String(length=64), nullable=True),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "persons",
        sa.Column("full_name", sa.String(length=200), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("phone", relay.db.crypto.EncryptedString(), nullable=True),
        sa.Column("dob", relay.db.crypto.EncryptedString(), nullable=True),
        sa.Column("pan", relay.db.crypto.EncryptedString(), nullable=True),
        sa.Column("aadhaar_last4", sa.String(length=4), nullable=True),
        sa.Column("bank_account", relay.db.crypto.EncryptedString(), nullable=True),
        sa.Column("ifsc", sa.String(length=11), nullable=True),
        sa.Column("photo_ref", sa.String(length=500), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "policy_versions",
        sa.Column("pack", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("tests_passed", sa.Boolean(), nullable=False),
        sa.Column("activated_by", sa.String(length=200), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("pack", "version"),
    )
    op.create_table(
        "workflow_definitions",
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("yaml", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "draft",
                "active",
                "retired",
                name="definition_status",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("activated_by", sa.String(length=200), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", "version"),
    )
    op.create_table(
        "workflow_runs",
        sa.Column("definition_name", sa.String(length=100), nullable=False),
        sa.Column("definition_version", sa.Integer(), nullable=False),
        sa.Column("subject_type", sa.String(length=32), nullable=False),
        sa.Column("subject_id", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "running",
                "paused",
                "waiting",
                "completed",
                "failed",
                "cancelled",
                name="run_status",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("context", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("started_by_actor", sa.String(length=200), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("trace_id", sa.String(length=32), nullable=True),
        sa.Column("parent_run_id", sa.UUID(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["parent_run_id"],
            ["workflow_runs.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_workflow_runs_subject", "workflow_runs", ["subject_type", "subject_id"], unique=False
    )
    op.create_table(
        "candidates",
        sa.Column("person_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("offer_id", sa.UUID(), nullable=True),
        sa.Column("external_ref", sa.String(length=64), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["person_id"],
            ["persons.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("external_ref"),
    )
    op.create_table(
        "consents",
        sa.Column("person_id", sa.UUID(), nullable=False),
        sa.Column("purpose_id", sa.String(length=64), nullable=False),
        sa.Column("notice_version", sa.String(length=32), nullable=False),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["person_id"],
            ["persons.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "data_requests",
        sa.Column("person_id", sa.UUID(), nullable=False),
        sa.Column(
            "type",
            sa.Enum(
                "access",
                "correction",
                "erasure",
                name="data_request_type",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["person_id"],
            ["persons.id"],
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["workflow_runs.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "documents",
        sa.Column("person_id", sa.UUID(), nullable=False),
        sa.Column(
            "category",
            sa.Enum(
                "pan",
                "aadhaar",
                "bank_proof",
                "education",
                "photo",
                "other",
                name="document_category",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("storage_ref", sa.String(length=500), nullable=False),
        sa.Column("extracted", relay.db.crypto.EncryptedJSON(), nullable=True),
        sa.Column("validation", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("retention_until", sa.Date(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["person_id"],
            ["persons.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "employees",
        sa.Column("person_id", sa.UUID(), nullable=False),
        sa.Column("employee_code", sa.String(length=32), nullable=True),
        sa.Column(
            "state",
            sa.Enum(
                "pre_hire",
                "onboarding",
                "active",
                "notice",
                "exited",
                name="employee_state",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("grade", sa.String(length=16), nullable=True),
        sa.Column("role_title", sa.String(length=200), nullable=True),
        sa.Column("department", sa.String(length=100), nullable=True),
        sa.Column("location", sa.String(length=100), nullable=True),
        sa.Column("manager_id", sa.UUID(), nullable=True),
        sa.Column("joining_date", sa.Date(), nullable=True),
        sa.Column("probation_end", sa.Date(), nullable=True),
        sa.Column("ctc_approved", relay.db.crypto.EncryptedJSON(), nullable=True),
        sa.Column("last_working_day", sa.Date(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["manager_id"],
            ["employees.id"],
        ),
        sa.ForeignKeyConstraint(
            ["person_id"],
            ["persons.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("employee_code"),
    )
    op.create_table(
        "step_runs",
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("step_id", sa.String(length=100), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "rule",
                "connector",
                "skill",
                "human",
                "wait",
                "parallel",
                name="step_kind",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING",
                "READY",
                "RUNNING",
                "SUCCEEDED",
                "WAITING_HUMAN",
                "WAITING_TIMER",
                "RETRY_SCHEDULED",
                "PARKED",
                "FAILED",
                "PAUSED",
                "SKIPPED",
                name="step_status",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "paused_from",
            sa.Enum(
                "PENDING",
                "READY",
                "RUNNING",
                "SUCCEEDED",
                "WAITING_HUMAN",
                "WAITING_TIMER",
                "RETRY_SCHEDULED",
                "PARKED",
                "FAILED",
                "PAUSED",
                "SKIPPED",
                name="step_paused_from",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=True,
        ),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_owner", sa.String(length=100), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("input", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("output", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("human_touched", sa.Boolean(), nullable=False),
        sa.Column("subject_actions", sa.Integer(), nullable=False),
        sa.Column("decision_id", sa.UUID(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["decision_id"],
            ["decisions.id"],
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["workflow_runs.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "step_id"),
    )
    op.create_index("ix_step_runs_ready", "step_runs", ["status", "next_attempt_at"], unique=False)
    op.create_table(
        "users",
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("slack_user_id", sa.String(length=32), nullable=True),
        sa.Column("person_id", sa.UUID(), nullable=True),
        sa.Column("roles", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["person_id"],
            ["persons.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
        sa.UniqueConstraint("slack_user_id"),
    )
    op.create_table(
        "conversations",
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("external_ref", sa.String(length=100), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "cost_events",
        sa.Column("run_id", sa.UUID(), nullable=True),
        sa.Column("step_run_id", sa.UUID(), nullable=True),
        sa.Column(
            "kind",
            sa.Enum(
                "llm",
                "ocr_cpu",
                "embedding_cpu",
                "infra_alloc",
                name="cost_kind",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("provider", sa.String(length=64), nullable=True),
        sa.Column("model_id", sa.String(length=100), nullable=True),
        sa.Column("tokens_in", sa.Integer(), nullable=False),
        sa.Column("tokens_out", sa.Integer(), nullable=False),
        sa.Column("unit_prices", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("cpu_seconds", sa.Numeric(precision=12, scale=4), nullable=False),
        sa.Column("cost_inr", sa.Numeric(precision=12, scale=4), nullable=False),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["workflow_runs.id"],
        ),
        sa.ForeignKeyConstraint(
            ["step_run_id"],
            ["step_runs.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_cost_events_run", "cost_events", ["run_id"], unique=False)
    op.create_table(
        "human_tasks",
        sa.Column("run_id", sa.UUID(), nullable=True),
        sa.Column("step_run_id", sa.UUID(), nullable=True),
        sa.Column(
            "kind",
            sa.Enum(
                "reserved_decision",
                "approval",
                "conflict",
                "exception",
                "low_confidence",
                "outage",
                "route_only",
                name="human_task_kind",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("assignee_role", sa.String(length=64), nullable=False),
        sa.Column("assignee_user_id", sa.UUID(), nullable=True),
        sa.Column("context_packet", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("options", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "open",
                "claimed",
                "decided",
                "expired",
                "cancelled",
                name="human_task_status",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("decision", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("decided_by", sa.UUID(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("sla_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["assignee_user_id"],
            ["users.id"],
        ),
        sa.ForeignKeyConstraint(
            ["decided_by"],
            ["users.id"],
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["workflow_runs.id"],
        ),
        sa.ForeignKeyConstraint(
            ["step_run_id"],
            ["step_runs.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_human_tasks_queue", "human_tasks", ["status", "assignee_role"], unique=False
    )
    op.create_table(
        "lifecycle_events",
        sa.Column("employee_id", sa.UUID(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["employee_id"],
            ["employees.id"],
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["workflow_runs.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "offers",
        sa.Column("candidate_id", sa.UUID(), nullable=False),
        sa.Column("ctc", relay.db.crypto.EncryptedJSON(), nullable=False),
        sa.Column("grade", sa.String(length=16), nullable=False),
        sa.Column("role_title", sa.String(length=200), nullable=True),
        sa.Column("department", sa.String(length=100), nullable=True),
        sa.Column("location", sa.String(length=100), nullable=True),
        sa.Column("joining_date", sa.Date(), nullable=False),
        sa.Column("approved_decision_id", sa.UUID(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["candidate_id"],
            ["candidates.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "timers",
        sa.Column("step_run_id", sa.UUID(), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "purpose",
            sa.Enum(
                "wait",
                "sla_reminder",
                "sla_escalate",
                "retry",
                name="timer_purpose",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("fired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["step_run_id"],
            ["step_runs.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_timers_due",
        "timers",
        ["due_at"],
        unique=False,
        postgresql_where=sa.text("fired_at IS NULL"),
    )
    op.create_table(
        "messages",
        sa.Column("conversation_id", sa.UUID(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("masked_text", sa.Text(), nullable=False),
        sa.Column("intent", sa.String(length=64), nullable=True),
        sa.Column("run_id", sa.UUID(), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["workflow_runs.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    # Circular references, added after both tables exist.
    op.create_foreign_key("fk_candidates_offer_id", "candidates", "offers", ["offer_id"], ["id"])
    op.create_foreign_key(
        "fk_offers_approved_decision_id", "offers", "human_tasks", ["approved_decision_id"], ["id"]
    )
    # Append-only guards and least-privilege roles (PRD 6.6, 10.3, 18).
    for stmt in upgrade_statements():
        op.execute(stmt)


def downgrade() -> None:
    for stmt in downgrade_statements():
        op.execute(stmt)
    op.drop_constraint("fk_candidates_offer_id", "candidates", type_="foreignkey")
    op.drop_constraint("fk_offers_approved_decision_id", "offers", type_="foreignkey")
    op.drop_table("messages")
    op.drop_index(
        "ix_timers_due", table_name="timers", postgresql_where=sa.text("fired_at IS NULL")
    )
    op.drop_table("timers")
    op.drop_table("offers")
    op.drop_table("lifecycle_events")
    op.drop_index("ix_human_tasks_queue", table_name="human_tasks")
    op.drop_table("human_tasks")
    op.drop_index("ix_cost_events_run", table_name="cost_events")
    op.drop_table("cost_events")
    op.drop_table("conversations")
    op.drop_table("users")
    op.drop_index("ix_step_runs_ready", table_name="step_runs")
    op.drop_table("step_runs")
    op.drop_table("employees")
    op.drop_table("documents")
    op.drop_table("data_requests")
    op.drop_table("consents")
    op.drop_table("candidates")
    op.drop_index("ix_workflow_runs_subject", table_name="workflow_runs")
    op.drop_table("workflow_runs")
    op.drop_table("workflow_definitions")
    op.drop_table("policy_versions")
    op.drop_table("persons")
    op.drop_table("model_payloads")
    op.drop_table("kill_switches")
    op.drop_table("idempotency_ledger")
    op.drop_table("decisions")
    op.drop_index("ix_audit_events_subject", table_name="audit_events")
    op.drop_index("ix_audit_events_run", table_name="audit_events")
    op.drop_table("audit_events")
