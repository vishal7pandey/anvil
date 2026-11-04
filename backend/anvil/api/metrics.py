from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    Column,
    MetaData,
    Numeric,
    Select,
    Table,
    create_engine,
    func,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Engine, make_url

from anvil.api.config import normalize_database_url

_SPEND_LOGS = Table(
    "LiteLLM_SpendLogs",
    MetaData(),
    Column("metadata", JSON().with_variant(JSONB(), "postgresql")),
    Column("spend", Numeric),
)


@dataclass(frozen=True)
class SpendTotals:
    model_calls: int
    usd: Decimal


def ensure_spend_log_index(engine: Engine) -> None:
    """Ensure the expression index on LiteLLM_SpendLogs(run_id) exists."""
    dialect = engine.url.get_backend_name()
    try:
        with engine.begin() as conn:
            if dialect == "postgresql":
                conn.execute(
                    text(
                        'CREATE INDEX IF NOT EXISTS ix_litellm_spend_logs_run_id '
                        'ON "LiteLLM_SpendLogs" (((metadata -> \'spend_logs_metadata\') ->> \'run_id\'));'
                    )
                )
            elif dialect == "sqlite":
                conn.execute(
                    text(
                        'CREATE INDEX IF NOT EXISTS ix_litellm_spend_logs_run_id '
                        'ON "LiteLLM_SpendLogs" (CAST(JSON_EXTRACT(JSON_QUOTE(JSON_EXTRACT(metadata, \'$.\"spend_logs_metadata\"\')), \'$.\"run_id\"\') AS VARCHAR));'
                    )
                )
    except Exception:
        # Table might not exist yet if LiteLLM has not initialized
        pass


def estimate_cost(
    input_tokens: int,
    output_tokens: int,
    events: list[Any] | None = None,
) -> float:
    """Estimate USD cost from token counts or usage events when spend rows lag."""
    if events:
        event_usd = sum(
            float(e.data.get("usd", 0) or 0)
            for e in events
            if getattr(e, "type", None) == "usage" and isinstance(getattr(e, "data", None), dict)
        )
        if event_usd > 0:
            return round(event_usd, 6)

    # Standard model blended rate: $3.00/1M input, $15.00/1M output
    est = (input_tokens * 3.0 + output_tokens * 15.0) / 1_000_000.0
    return round(est, 6)


def create_litellm_engine(database_url: str) -> Engine | None:
    url = make_url(normalize_database_url(database_url))
    if url.get_backend_name() != "postgresql":
        return None
    engine = create_engine(url.set(database="litellm"), pool_pre_ping=True)
    ensure_spend_log_index(engine)
    return engine


def build_spend_totals_statement(run_ids: list[UUID]) -> Select:
    run_id_field = _SPEND_LOGS.c.metadata["spend_logs_metadata"]["run_id"].as_string()
    return (
        select(
            run_id_field.label("run_id"),
            func.count().label("model_calls"),
            func.coalesce(func.sum(_SPEND_LOGS.c.spend), 0).label("usd"),
        )
        .where(run_id_field.in_([str(run_id) for run_id in run_ids]))
        .group_by(run_id_field)
    )


def load_litellm_spend_totals(engine: Engine, run_ids: list[UUID]) -> dict[str, SpendTotals]:
    if not run_ids:
        return {}

    statement = build_spend_totals_statement(run_ids)
    with engine.connect() as connection:
        rows = connection.execute(statement).mappings()
        return {
            row["run_id"]: SpendTotals(
                model_calls=int(row["model_calls"]),
                usd=Decimal(str(row["usd"])),
            )
            for row in rows
        }
