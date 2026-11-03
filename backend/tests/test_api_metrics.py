import json
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import create_engine

from anvil.api.metrics import (
    SpendTotals,
    create_litellm_engine,
    load_litellm_spend_totals,
)


def test_litellm_engine_targets_gateway_database():
    engine = create_litellm_engine(
        "postgresql+psycopg://anvil:example@localhost:5433/anvil_copilot"
    )

    assert engine is not None
    assert engine.url.database == "litellm"
    engine.dispose()


def test_load_litellm_spend_totals_filters_by_run_metadata():
    engine = create_engine("sqlite://")
    run_id = uuid4()
    other_run_id = uuid4()
    with engine.begin() as connection:
        connection.exec_driver_sql(
            'CREATE TABLE "LiteLLM_SpendLogs" (metadata JSON, spend NUMERIC)'
        )
        connection.exec_driver_sql(
            'INSERT INTO "LiteLLM_SpendLogs" (metadata, spend) VALUES '
            '(?, ?), (?, ?), (?, ?), (?, ?)',
            (
                json.dumps({"spend_logs_metadata": {"run_id": str(run_id)}}),
                0.02,
                json.dumps({"spend_logs_metadata": {"run_id": str(run_id)}}),
                0.03,
                json.dumps({"spend_logs_metadata": {"run_id": str(other_run_id)}}),
                0.99,
                json.dumps({"run_id": str(run_id)}),
                100,
            ),
        )

    totals = load_litellm_spend_totals(engine, [run_id])

    assert totals == {
        str(run_id): SpendTotals(model_calls=2, usd=Decimal("0.05")),
    }
    engine.dispose()
