"""Database-level protections (PRD 6.6, 10.3, 18).

- A trigger rejects UPDATE, DELETE and TRUNCATE on append-only tables.
- Least-privilege group roles: `relay_app` (api + worker) gets INSERT/SELECT only on append-only
  tables, and `relay_readonly_auditor` gets SELECT only. Login users are granted these roles.
"""

from __future__ import annotations

from relay.db.models import APPEND_ONLY_TABLES

GUARD_FUNCTION = """
CREATE OR REPLACE FUNCTION relay_reject_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'relay: % on append-only table % is not allowed', TG_OP, TG_TABLE_NAME
        USING ERRCODE = 'insufficient_privilege';
END;
$$ LANGUAGE plpgsql;
"""

ROLES = """
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'relay_app') THEN
        CREATE ROLE relay_app NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'relay_readonly_auditor') THEN
        CREATE ROLE relay_readonly_auditor NOLOGIN;
    END IF;
END $$;
"""


def upgrade_statements() -> list[str]:
    """SQL to install guards and role grants."""
    stmts = [GUARD_FUNCTION, ROLES]
    for t in APPEND_ONLY_TABLES:
        stmts += [
            f"CREATE TRIGGER {t}_no_update_delete BEFORE UPDATE OR DELETE ON {t} "
            "FOR EACH ROW EXECUTE FUNCTION relay_reject_mutation();",
            f"CREATE TRIGGER {t}_no_truncate BEFORE TRUNCATE ON {t} "
            "FOR EACH STATEMENT EXECUTE FUNCTION relay_reject_mutation();",
        ]
    stmts += [
        "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO relay_app;",
        "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO relay_app;",
        "GRANT SELECT ON ALL TABLES IN SCHEMA public TO relay_readonly_auditor;",
    ]
    for t in APPEND_ONLY_TABLES:
        stmts.append(f"REVOKE UPDATE, DELETE, TRUNCATE ON {t} FROM relay_app;")
    return stmts


def downgrade_statements() -> list[str]:
    """SQL to remove guards (roles are left in place; they may own grants elsewhere)."""
    stmts = []
    for t in APPEND_ONLY_TABLES:
        stmts += [
            f"DROP TRIGGER IF EXISTS {t}_no_update_delete ON {t};",
            f"DROP TRIGGER IF EXISTS {t}_no_truncate ON {t};",
        ]
    stmts.append("DROP FUNCTION IF EXISTS relay_reject_mutation();")
    return stmts
