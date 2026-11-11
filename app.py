# -*- coding: utf-8 -*-
"""Affiliate Partnerships Analyst — Streamlit chat UI.

Ask a plain-English partnerships question; the agent writes DuckDB SQL against a governed
semantic layer, runs it, and shows the answer, the table, the SQL, and a reasoning trace.
All data is synthetic. Live answers use Claude Haiku via an API key in Streamlit secrets;
without a key the app runs in offline mode that still shows the trusted canonical answers.
"""
from __future__ import annotations

import os
import tempfile

import duckdb
import pandas as pd
import streamlit as st

from partnerships_agent.data_gen import build_database, REFERENCE_DATE
from partnerships_agent.agent import PartnershipsAgent, ClaudeLLM, SqlSafetyError
from partnerships_agent.golden import ANSWERABLE

MAX_QUESTIONS_PER_SESSION = 25
EXAMPLES = [
    "gross profit last 30 days",
    "gross profit by property last 30 days",
    "top 5 merchants by commission last 30 days",
    "eCPC for shopmart last 30 days",
    "realized vs benchmark rate for shopmart",
    "commission this month vs last month",
    "orders from a cashback click last 30 days",
    "which partners should we drop?",
]

st.set_page_config(page_title="Affiliate Partnerships Analyst", page_icon="📊", layout="wide")


@st.cache_resource(show_spinner=False)
def _db_path() -> str:
    path = os.path.join(tempfile.gettempdir(), "partnerships_demo.duckdb")
    if os.path.exists(path):
        os.remove(path)
    con = build_database(path)
    con.close()
    return path


def _connect() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(_db_path(), read_only=True)


def _get_llm():
    key = None
    try:
        key = st.secrets.get("ANTHROPIC_API_KEY")
    except Exception:
        key = None
    key = key or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return None
    return ClaudeLLM(api_key=key)


def _canonical_for(question: str):
    for q in ANSWERABLE:
        if q["question"] == question:
            return q
    return None


def _as_frame(columns, rows) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=columns)


def _maybe_chart(df: pd.DataFrame) -> None:
    if df.shape[1] == 2 and len(df) > 1 and pd.api.types.is_numeric_dtype(df.iloc[:, 1]):
        st.bar_chart(df.set_index(df.columns[0]))


def _render_result(res) -> None:
    if res.refused:
        st.info(res.answer)
        with st.expander("Why it refused"):
            st.write(f"Reason: `{res.refusal_reason}` — the agent explains and reconciles; "
                     "it does not recommend actions or forecast.")
        return
    st.success(res.answer)
    if res.rows is not None:
        df = _as_frame(res.columns, res.rows)
        _maybe_chart(df)
        with st.expander("Result table", expanded=True):
            st.dataframe(df, width="stretch", hide_index=True)
    if res.sql:
        with st.expander("SQL the agent wrote"):
            st.code(res.sql.strip(), language="sql")
    if res.trace:
        with st.expander("Reasoning trace"):
            for step in res.trace:
                st.markdown(f"- {step}" if "\n" not in step else step)
    if res.caveat:
        st.caption(f"⚠️ {res.caveat}")


def _offline_answer(question: str, con) -> None:
    q = _canonical_for(question)
    if not q:
        st.warning("Offline mode shows the built-in example answers. Add an Anthropic API key "
                   "in Streamlit secrets to ask your own questions live.")
        return
    cur = con.execute(q["sql"])
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
    st.success("Offline mode: showing the trusted canonical answer for this example.")
    df = _as_frame(cols, rows)
    _maybe_chart(df)
    with st.expander("Result table", expanded=True):
        st.dataframe(df, width="stretch", hide_index=True)
    with st.expander("Canonical SQL"):
        st.code(q["sql"].strip(), language="sql")
    st.caption(f"Note: {q['note']}")


def _run(question: str) -> None:
    st.session_state.setdefault("count", 0)
    if st.session_state["count"] >= MAX_QUESTIONS_PER_SESSION:
        st.warning("Session question limit reached. Reload to start over.")
        return
    st.session_state["count"] += 1

    st.markdown(f"#### ❓ {question}")
    con = _connect()
    try:
        llm = _get_llm()
        if llm is None:
            _offline_answer(question, con)
            return
        agent = PartnershipsAgent(con, llm)
        with st.spinner("Writing SQL and reconciling…"):
            res = agent.answer(question)
        _render_result(res)
    except SqlSafetyError as e:
        st.error(f"Blocked an unsafe query: {e}")
    except Exception as e:  # keep the demo resilient
        st.error(f"Something went wrong answering that: {e}")
    finally:
        con.close()


# ---- layout ----
st.title("📊 Affiliate Partnerships Analyst")
st.caption(
    "Ask a partnerships question in plain English. The agent writes SQL against a governed "
    "semantic layer, runs it, and shows the answer, the table, the SQL, and its reasoning. "
    f"All data is synthetic; the dataset's “today” is {REFERENCE_DATE}."
)

with st.sidebar:
    st.header("About")
    st.markdown(
        "- **Natural-language → SQL** over a governed semantic layer (partner vs merchant, a "
        "`property` default, cost at its own grain).\n"
        "- It **explains and reconciles** — it does **not** recommend actions or forecast, and "
        "it refuses out-of-scope questions.\n"
        "- Answers **tie out** to trusted numbers on a set of golden questions.\n"
        "- 100% synthetic, de-branded demo."
    )
    if _get_llm() is None:
        st.warning("Offline mode: no API key set. Example questions show the trusted canonical "
                   "answers. Add `ANTHROPIC_API_KEY` to enable live, type-your-own questions.")
    else:
        st.success("Live mode: ask your own questions below.")

st.subheader("Try an example")
cols = st.columns(2)
for i, ex in enumerate(EXAMPLES):
    if cols[i % 2].button(ex, key=f"ex_{i}", width="stretch"):
        _run(ex)

st.subheader("Or ask your own")
typed = st.chat_input("e.g. conversion rate for shopmart last 30 days")
if typed:
    _run(typed)
