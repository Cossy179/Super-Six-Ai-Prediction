"""Database-level immutability for locked prediction runs (DESIGN.md §6.1, §14.2).

The ORM ``before_flush`` guard in ``models.py`` catches ordinary edits, but bulk
``UPDATE``/``DELETE`` statements and raw SQL bypass it. These triggers make the database
itself refuse to change a LOCKED or SETTLED run's body, or the run row (other than the
LOCKED → SETTLED transition and its ``settled_at`` timestamp).
"""

from __future__ import annotations

from sqlalchemy.engine import Connection

BODY_TABLES = ("fixture_forecasts", "score_probabilities", "recommendations",
               "golden_goal_predictions")
FROZEN_COLUMNS = ("id", "round_id", "model_registry_id", "cutoff_at", "created_at",
                  "code_version", "rules_version", "input_fingerprint", "config", "warnings",
                  "block_reasons", "published_at", "locked_at", "supersedes_run_id",
                  "correction_reason")
_FROZEN = "('LOCKED', 'SETTLED')"


def _sqlite() -> list[str]:
    stmts = []
    for table in BODY_TABLES:
        for op, ref in (("INSERT", "NEW"), ("UPDATE", "OLD"), ("DELETE", "OLD")):
            stmts.append(
                f"CREATE TRIGGER IF NOT EXISTS trg_{table}_{op.lower()}_locked "
                f"BEFORE {op} ON {table} "
                f"WHEN (SELECT status FROM prediction_runs WHERE id = {ref}.run_id) IN {_FROZEN} "
                "BEGIN SELECT RAISE(ABORT, 'prediction run is locked; its records are "
                "immutable'); END"
            )
    unchanged = " AND ".join(f"NEW.{c} IS OLD.{c}" for c in FROZEN_COLUMNS)
    stmts.append(
        "CREATE TRIGGER IF NOT EXISTS trg_prediction_runs_update_locked "
        "BEFORE UPDATE ON prediction_runs "
        f"WHEN OLD.status IN {_FROZEN} AND NOT (OLD.status = 'LOCKED' AND "
        f"NEW.status IN ('LOCKED', 'SETTLED') AND {unchanged}) "
        "BEGIN SELECT RAISE(ABORT, 'prediction run is locked'); END"
    )
    stmts.append(
        "CREATE TRIGGER IF NOT EXISTS trg_prediction_runs_delete_locked "
        "BEFORE DELETE ON prediction_runs "
        f"WHEN OLD.status IN {_FROZEN} "
        "BEGIN SELECT RAISE(ABORT, 'prediction run is locked'); END"
    )
    return stmts


def _postgres() -> list[str]:
    old_row = ", ".join(f"OLD.{c}::text" for c in FROZEN_COLUMNS)
    new_row = ", ".join(f"NEW.{c}::text" for c in FROZEN_COLUMNS)
    stmts = [
        """
        CREATE OR REPLACE FUNCTION super6_guard_run_body() RETURNS trigger AS $$
        DECLARE rid text; st text;
        BEGIN
          IF TG_OP = 'DELETE' THEN rid := OLD.run_id; ELSE rid := NEW.run_id; END IF;
          SELECT status INTO st FROM prediction_runs WHERE id = rid;
          IF st IN ('LOCKED', 'SETTLED') THEN
            RAISE EXCEPTION USING MESSAGE =
              'prediction run ' || rid || ' is ' || st || '; its records are immutable';
          END IF;
          IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
          RETURN NEW;
        END $$ LANGUAGE plpgsql
        """,
        f"""
        CREATE OR REPLACE FUNCTION super6_guard_run() RETURNS trigger AS $$
        BEGIN
          IF OLD.status IN {_FROZEN} THEN
            IF TG_OP = 'DELETE' THEN
              RAISE EXCEPTION USING MESSAGE = 'prediction run ' || OLD.id || ' is locked';
            END IF;
            IF NOT (OLD.status = 'LOCKED' AND NEW.status IN ('LOCKED', 'SETTLED'))
               OR ROW({new_row}) IS DISTINCT FROM ROW({old_row}) THEN
              RAISE EXCEPTION USING MESSAGE = 'prediction run ' || OLD.id || ' is locked';
            END IF;
          END IF;
          IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
          RETURN NEW;
        END $$ LANGUAGE plpgsql
        """,
    ]
    for table in BODY_TABLES:
        stmts.append(f"DROP TRIGGER IF EXISTS trg_{table}_locked ON {table}")
        stmts.append(f"CREATE TRIGGER trg_{table}_locked BEFORE INSERT OR UPDATE OR DELETE "
                     f"ON {table} FOR EACH ROW EXECUTE FUNCTION super6_guard_run_body()")
    stmts.append("DROP TRIGGER IF EXISTS trg_prediction_runs_locked ON prediction_runs")
    stmts.append("CREATE TRIGGER trg_prediction_runs_locked BEFORE UPDATE OR DELETE ON "
                 "prediction_runs FOR EACH ROW EXECUTE FUNCTION super6_guard_run()")
    return stmts


def install(conn: Connection) -> None:
    dialect = conn.dialect.name
    if dialect == "sqlite":
        stmts = _sqlite()
    elif dialect == "postgresql":
        stmts = _postgres()
    else:
        raise NotImplementedError(f"no immutability triggers for {dialect}")
    for stmt in stmts:
        conn.exec_driver_sql(stmt)


def uninstall(conn: Connection) -> None:
    if conn.dialect.name == "sqlite":
        names = [f"trg_{t}_{op}_locked" for t in BODY_TABLES
                 for op in ("insert", "update", "delete")]
        names += ["trg_prediction_runs_update_locked", "trg_prediction_runs_delete_locked"]
        for name in names:
            conn.exec_driver_sql(f"DROP TRIGGER IF EXISTS {name}")
    elif conn.dialect.name == "postgresql":
        for table in BODY_TABLES:
            conn.exec_driver_sql(f"DROP TRIGGER IF EXISTS trg_{table}_locked ON {table}")
        conn.exec_driver_sql("DROP TRIGGER IF EXISTS trg_prediction_runs_locked ON "
                             "prediction_runs")
        conn.exec_driver_sql("DROP FUNCTION IF EXISTS super6_guard_run_body()")
        conn.exec_driver_sql("DROP FUNCTION IF EXISTS super6_guard_run()")
