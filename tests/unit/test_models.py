from __future__ import annotations

from relay.db.models import APPEND_ONLY_TABLES, Base

PRD_TABLES = {
    # 10.1
    "persons", "employees", "candidates", "offers", "lifecycle_events", "documents",
    "consents", "data_requests", "users",
    # 10.2
    "workflow_definitions", "workflow_runs", "step_runs", "timers", "idempotency_ledger",
    "human_tasks", "decisions", "kill_switches", "policy_versions", "conversations", "messages",
    # 10.3
    "audit_events", "model_payloads", "cost_events",
}  # fmt: skip


def test_every_prd_table_is_modelled() -> None:
    assert set(Base.metadata.tables) >= PRD_TABLES


def test_append_only_tables_have_no_updated_at() -> None:
    for name in APPEND_ONLY_TABLES:
        assert "updated_at" not in Base.metadata.tables[name].c


def test_sensitive_columns_are_encrypted() -> None:
    from relay.db.crypto import EncryptedJSON, EncryptedString

    sensitive = {
        "persons": ["phone", "dob", "pan", "bank_account"],
        "employees": ["ctc_approved"],
        "offers": ["ctc"],
        "documents": ["extracted"],
    }
    for table, cols in sensitive.items():
        for col in cols:
            col_type = Base.metadata.tables[table].c[col].type
            assert isinstance(col_type, EncryptedString | EncryptedJSON), f"{table}.{col}"


def test_step_runs_unique_per_run_and_step() -> None:
    uniques = {
        tuple(c.name for c in con.columns)
        for con in Base.metadata.tables["step_runs"].constraints
        if con.__class__.__name__ == "UniqueConstraint"
    }
    assert ("run_id", "step_id") in uniques
