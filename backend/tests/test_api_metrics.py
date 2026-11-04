import json
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import create_engine, text

from anvil.api.metrics import (
    SpendTotals,
    build_spend_totals_statement,
    create_litellm_engine,
    ensure_spend_log_index,
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


def test_load_litellm_spend_totals_query_plan_uses_index():
    engine = create_engine("sqlite://")
    run_id = uuid4()
    with engine.begin() as connection:
        connection.exec_driver_sql(
            'CREATE TABLE "LiteLLM_SpendLogs" (metadata JSON, spend NUMERIC)'
        )
    ensure_spend_log_index(engine)

    statement = build_spend_totals_statement([run_id])
    compiled_sql = str(statement.compile(engine, compile_kwargs={"literal_binds": True}))

    with engine.connect() as connection:
        plan_rows = connection.execute(text(f"EXPLAIN QUERY PLAN {compiled_sql}")).fetchall()
        plan_str = " ".join(str(row) for row in plan_rows)
        assert "USING INDEX ix_litellm_spend_logs_run_id" in plan_str

    engine.dispose()


def test_load_litellm_spend_totals_retries_index_lazily():
    engine = create_engine("sqlite://")

    # Initially table does not exist; fails and logs warning
    assert ensure_spend_log_index(engine) is False
    assert not getattr(engine, "_spend_log_index_ensured", False)

    # Now table is created (e.g. by LiteLLM)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            'CREATE TABLE "LiteLLM_SpendLogs" (metadata JSON, spend NUMERIC)'
        )

    # load_litellm_spend_totals retries lazily and succeeds
    run_id = uuid4()
    load_litellm_spend_totals(engine, [run_id])
    assert getattr(engine, "_spend_log_index_ensured", False) is True

    engine.dispose()
