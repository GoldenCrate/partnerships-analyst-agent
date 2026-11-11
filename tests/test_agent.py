# -*- coding: utf-8 -*-
"""Agent-pipeline tests with a scripted LLM (no API key). Proves the loop executes grounded
SQL, surfaces guardrails in the trace, refuses correctly, and blocks unsafe SQL."""
import pytest

from partnerships_agent.data_gen import build_database
from partnerships_agent.agent import (
    PartnershipsAgent, ScriptedLLM, validate_sql, SqlSafetyError,
)
from partnerships_agent.semantic import load_semantic, view_names
from partnerships_agent.golden import ANSWERABLE, REFUSALS


def _sql(qid: str) -> str:
    return next(q["sql"] for q in ANSWERABLE if q["id"] == qid)


@pytest.fixture(scope="module")
def con():
    return build_database()


def test_answerable_executes_grounded_sql(con):
    plan = {"in_scope": True, "refusal_reason": None,
            "plan": "REVENUE_SUMMARY, filter default property us_main, true gross profit.",
            "sql": _sql("gross_profit_30d_default"), "caveat": None}
    llm = ScriptedLLM([plan, "Gross profit over the last 30 days on us_main was $2,952.37."])
    agent = PartnershipsAgent(con, llm)
    res = agent.answer("gross profit last 30 days")

    assert not res.refused
    assert res.rows == [(2952.37,)]
    assert res.columns == ["true_gross_profit"]
    assert "2,952.37" in res.answer
    assert any("named default property" in t for t in res.trace)


def test_independent_aggregation_shows_in_trace(con):
    plan = {"in_scope": True, "refusal_reason": None,
            "plan": "AD_SPEND and EFFECTIVE_CLICKS aggregated independently, divide.",
            "sql": _sql("ecpc_merchant_30d"), "caveat": None}
    llm = ScriptedLLM([plan, "eCPC for shopmart on us_main over the last 30 days was 0.5787."])
    agent = PartnershipsAgent(con, llm)
    res = agent.answer("eCPC for shopmart last 30 days")

    assert res.rows == [(0.5787,)]
    assert any("aggregated each source independently" in t for t in res.trace)


def test_followup_history_is_injected_into_plan(con):
    """A follow-up carries prior turns into the planning prompt so references resolve; a
    question asked in isolation gets the plain prompt (no conversation block)."""
    sql = _sql("rpm_by_ad_partner_30d")
    plan = {"in_scope": True, "refusal_reason": None,
            "plan": "RPM per ad_partner: ad_revenue by ad_partner over total page views.",
            "sql": sql, "caveat": None}
    llm = ScriptedLLM([plan, "RPM by ad partner on us_main: baseline_network leads."])
    agent = PartnershipsAgent(con, llm)
    res = agent.answer(
        "now show RPM by ad partner for the same period",
        history=[{"question": "eCPM by ad partner last 30 days",
                  "answer": "eCPM was $11.13 for premium_managed over 2026-10-02 to 2026-10-31."}],
    )
    assert not res.refused
    plan_prompt = llm.calls[0][1]                     # ("json", user_prompt)
    assert "CONVERSATION SO FAR" in plan_prompt
    assert "eCPM by ad partner last 30 days" in plan_prompt

    # isolation: no history -> no conversation block
    llm2 = ScriptedLLM([plan, "ok"])
    PartnershipsAgent(con, llm2).answer("RPM by ad partner last 30 days")
    assert "CONVERSATION SO FAR" not in llm2.calls[0][1]


@pytest.mark.parametrize("r", REFUSALS, ids=lambda r: r["id"])
def test_refusals(con, r):
    plan = {"in_scope": False, "refusal_reason": r["reason"], "plan": None, "sql": None}
    agent = PartnershipsAgent(con, ScriptedLLM([plan]))
    res = agent.answer(r["question"])

    assert res.refused
    assert res.refusal_reason == r["reason"]
    assert res.sql is None
    assert res.answer  # a real refusal message


def test_unsafe_sql_is_blocked(con):
    plan = {"in_scope": True, "refusal_reason": None, "plan": "bad",
            "sql": "DELETE FROM REVENUE_SUMMARY", "caveat": None}
    agent = PartnershipsAgent(con, ScriptedLLM([plan]))
    with pytest.raises(SqlSafetyError):
        agent.answer("drop everything")


def test_validate_sql_rules():
    views = view_names(load_semantic())
    validate_sql("SELECT 1 FROM REVENUE_SUMMARY", views)                 # ok
    with pytest.raises(SqlSafetyError):
        validate_sql("UPDATE REVENUE_SUMMARY SET orders = 0", views)     # DML
    with pytest.raises(SqlSafetyError):
        validate_sql("SELECT 1; DROP TABLE CLICKS", views)               # multi-stmt
    with pytest.raises(SqlSafetyError):
        validate_sql("SELECT * FROM SECRET_TABLE", views)                # unknown view
