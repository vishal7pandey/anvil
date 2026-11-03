from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from sqlalchemy import JSON, Column, MetaData, Numeric, Table, create_engine, func, select
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


def create_litellm_engine(database_url: str) -> Engine | None:
    url = make_url(normalize_database_url(database_url))
    if url.get_backend_name() != "postgresql":
        return None
    return create_engine(url.set(database="litellm"), pool_pre_ping=True)


def load_litellm_spend_totals(engine: Engine, run_ids: list[UUID]) -> dict[str, SpendTotals]:
    if not run_ids:
        return {}

    run_id_field = _SPEND_LOGS.c.metadata["spend_logs_metadata"]["run_id"].as_string()
    statement = (
        select(
            run_id_field.label("run_id"),
            func.count().label("model_calls"),
            func.coalesce(func.sum(_SPEND_LOGS.c.spend), 0).label("usd"),
        )
        .where(run_id_field.in_([str(run_id) for run_id in run_ids]))
        .group_by(run_id_field)
    )

    with engine.connect() as connection:
        rows = connection.execute(statement).mappings()
        return {
            row["run_id"]: SpendTotals(
                model_calls=int(row["model_calls"]),
                usd=Decimal(str(row["usd"])),
            )
            for row in rows
        }
