# -*- coding: utf-8 -*-
"""Affiliate Partnerships Analyst — conversational chat UI.

Ask a plain-English partnerships question. The agent writes DuckDB SQL against a governed
semantic layer, runs it, and answers — and you watch it think: it states each step (scope
check, plan, the SQL it wrote, running it, writing the answer) live as it works.

All data is synthetic. Live answers use Claude Haiku via an API key in Streamlit secrets;
without a key the app runs an offline mode that still shows the trusted canonical answers.
"""
from __future__ import annotations

import os
import tempfile

import duckdb
import pandas as pd
import streamlit as st

from partnerships_agent.data_gen import build_database, REFERENCE_DATE, SCHEMA_VERSION
from partnerships_agent.agent import PartnershipsAgent, ClaudeLLM, SqlSafetyError
from partnerships_agent.golden import ANSWERABLE

MAX_QUESTIONS_PER_SESSION = 25
EXAMPLES = [
    "gross profit by property last 30 days",
    "RPM last 30 days",
    "ad impressions last 30 days",
    "ad CTR for shopmart last 30 days",
    "eCPM by ad partner last 30 days",
    "top 5 merchants by commission last 30 days",
    "commission this month vs last month",
    "which partners should we drop?",
]

st.set_page_config(page_title="Affiliate Partnerships Analyst", page_icon="📊", layout="centered")


@st.cache_resource(show_spinner=False)
def _db_path(version: int) -> str:
    # Filename and cache key both include the schema version, so a schema change rebuilds a
    # fresh DB instead of reusing a stale cached file across a redeploy.
    path = os.path.join(tempfile.gettempdir(), f"partnerships_demo_v{version}.duckdb")
    if os.path.exists(path):
        os.remove(path)
    con = build_database(path)
    con.close()
    return path


def _connect() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(_db_path(SCHEMA_VERSION), read_only=True)


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


# ---- rendering a stored assistant turn -------------------------------------------------

def _render_thinking(steps: list) -> None:
    if not steps:
        return
    with st.expander("🧠 How it worked this out", expanded=False):
        for s in steps:
            st.markdown(s)


def _render_assistant(entry: dict) -> None:
    mode = entry.get("mode")
    if mode == "error":
        st.error(entry["answer"])
        return
    if mode == "offline_notice":
        st.info(entry["answer"])
        return
    if entry.get("refused"):
        st.markdown(entry["answer"])
        with st.expander("Why it declined"):
            st.write(f"Reason: `{entry.get('refusal_reason')}` — the agent explains and "
                     "reconciles; it does not recommend actions or forecast.")
        _render_thinking(entry.get("steps", []))
        return

    st.markdown(entry["answer"])
    if entry.get("rows") is not None and entry.get("columns"):
        df = _as_frame(entry["columns"], entry["rows"])
        _maybe_chart(df)
        with st.expander("Result table", expanded=True):
            st.dataframe(df, width="stretch", hide_index=True)
    if entry.get("sql"):
        with st.expander("SQL the agent wrote"):
            st.code(entry["sql"].strip(), language="sql")
    _render_thinking(entry.get("steps", []))
    if entry.get("caveat"):
        st.caption(f"⚠️ {entry['caveat']}")
    if mode == "offline":
        st.caption("Offline mode: trusted canonical answer for this example "
                   "(no API key set, so the live agent isn't running).")


# ---- producing a new assistant turn ----------------------------------------------------

def _history_pairs(messages) -> list:
    """Build [{question, answer}] pairs from stored turns so the agent can resolve follow-ups.
    Keeps every prior question; drops the answer text of refusals/errors (unhelpful as context)."""
    pairs = []
    pending_q = None
    for m in messages:
        if m["role"] == "user":
            pending_q = m["text"]
        elif pending_q is not None:
            ans = "" if m.get("refused") or m.get("mode") == "error" else m.get("answer", "")
            pairs.append({"question": pending_q, "answer": ans})
            pending_q = None
    return pairs


def _answer_live(question: str, con, llm, history=None) -> dict:
    """Run the agent, streaming its thinking into a live status box; return a stored entry."""
    steps: list[str] = []
    status = st.status("Thinking…", expanded=True)

    def progress(msg: str) -> None:
        steps.append(msg)
        status.markdown(msg)

    try:
        agent = PartnershipsAgent(con, llm)
        res = agent.answer(question, progress=progress, history=history)
    except SqlSafetyError as e:
        status.update(label="Blocked an unsafe query", state="error", expanded=False)
        return {"role": "assistant", "mode": "error",
                "answer": f"I blocked that query for safety: {e}", "steps": steps}
    except Exception as e:  # keep the demo resilient
        status.update(label="Something went wrong", state="error", expanded=False)
        return {"role": "assistant", "mode": "error",
                "answer": f"Something went wrong answering that: {e}", "steps": steps}

    status.update(label="Done", state="complete", expanded=False)
    return {
        "role": "assistant",
        "mode": "refused" if res.refused else "live",
        "refused": res.refused,
        "refusal_reason": res.refusal_reason,
        "answer": res.answer,
        "columns": res.columns,
        "rows": res.rows,
        "sql": res.sql,
        "caveat": res.caveat,
        "steps": steps,
    }


def _answer_offline(question: str, con) -> dict:
    q = _canonical_for(question)
    if not q:
        return {"role": "assistant", "mode": "offline_notice",
                "answer": "Offline mode shows the built-in example answers. Add an Anthropic "
                          "API key in Streamlit secrets to ask your own questions live. Try one "
                          "of the example questions above."}
    cur = con.execute(q["sql"])
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
    return {"role": "assistant", "mode": "offline", "refused": False,
            "answer": "Showing the trusted canonical answer for this example.",
            "columns": cols, "rows": rows, "sql": q["sql"], "caveat": q["note"],
            "steps": [f"Offline: ran the locked canonical SQL for `{q['id']}`."]}


def _submit(question: str) -> None:
    """Queue a user question and rerun. The answer is produced on the next run, so the
    example chips and the first-run intro disappear the instant a question is asked."""
    question = (question or "").strip()
    if not question:
        return
    st.session_state.setdefault("count", 0)
    if st.session_state["count"] >= MAX_QUESTIONS_PER_SESSION:
        st.session_state["limit_hit"] = True
        return
    st.session_state["count"] += 1
    st.session_state["messages"].append({"role": "user", "text": question, "answered": False})
    st.rerun()


# ---- layout ----------------------------------------------------------------------------

st.title("📊 Affiliate Partnerships Analyst")
st.caption(
    "Ask a partnerships question in plain English. The agent writes SQL against a governed "
    "semantic layer, runs it, and answers — and you can watch it think as it works. "
    f"All data is synthetic; the dataset's “today” is {REFERENCE_DATE}."
)

with st.sidebar:
    st.header("About")
    st.markdown(
        "- **Natural-language → SQL** over a governed semantic layer (affiliate `partner` vs "
        "`ad_partner` vs merchant, a `property` default, cost and impressions at their own grain).\n"
        "- Covers **affiliate** metrics (commission, gross profit, EPC, conversion) **and ad** "
        "metrics (impressions, CTR, CPC, eCPM, RPM) — the latter split across **ad partners** "
        "(premium_managed / open_exchange / baseline_network) with very different eCPM.\n"
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
    if st.button("Clear conversation", width="stretch"):
        st.session_state["messages"] = []
        st.session_state["count"] = 0
        st.rerun()

st.session_state.setdefault("messages", [])
msgs = st.session_state["messages"]

# Render the conversation so far as one continuous thread.
for m in msgs:
    with st.chat_message(m["role"]):
        if m["role"] == "user":
            st.markdown(m["text"])
        else:
            _render_assistant(m)

# If the last turn is a question we haven't answered yet, answer it now (streaming its
# thinking live). It was appended on the previous run, so the chips are already gone.
if msgs and msgs[-1]["role"] == "user" and not msgs[-1].get("answered"):
    question = msgs[-1]["text"]
    msgs[-1]["answered"] = True
    history = _history_pairs(msgs[:-1])
    con = _connect()
    try:
        with st.chat_message("assistant"):
            llm = _get_llm()
            entry = (_answer_offline(question, con) if llm is None
                     else _answer_live(question, con, llm, history))
            _render_assistant(entry)
    finally:
        con.close()
    msgs.append(entry)

if st.session_state.pop("limit_hit", False):
    st.warning("Session question limit reached — reload the page to start a new conversation.")

# First-run suggestions: shown ONLY before the first question, then they disappear for good.
if not msgs:
    st.markdown("#### 👋 Try one of these — or type your own below")
    st.caption("Pick a question to see the agent think through it. Then keep going: ask a "
               "follow-up or dig deeper, and the conversation builds on itself.")
    cols = st.columns(2)
    for i, ex in enumerate(EXAMPLES):
        if cols[i % 2].button(ex, key=f"ex_{i}", width="stretch"):
            _submit(ex)

typed = st.chat_input("Ask a follow-up or a new question — e.g. “now break that down by property”")
if typed:
    _submit(typed)
