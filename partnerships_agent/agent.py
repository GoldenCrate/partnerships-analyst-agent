# -*- coding: utf-8 -*-
"""The Affiliate Partnerships Analyst agent.

Pipeline: scope-check -> plan -> text-to-SQL -> execute -> narrate. It explains and reconciles;
it never recommends actions, and it refuses out-of-scope / forecast / recommendation asks.

The LLM is behind a small seam (`LLM`): `ClaudeLLM` calls Claude Haiku for the live app;
`ScriptedLLM` replays canned responses so the whole pipeline is testable without an API key.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Protocol

from .semantic import load_semantic, render_layer, view_names

MODEL = "claude-haiku-5-5"

_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|attach|copy|pragma|replace|truncate|"
    r"grant|revoke|call|export|install|load|set|vacuum|checkpoint)\b",
    re.IGNORECASE,
)

REFUSAL_TEXT = {
    "explain_not_recommend": (
        "I reconcile and explain the numbers, but I don't make recommendations like which "
        "partners to drop — that's a decision for the team. I can show the data behind it "
        "instead, for example commission, effective clicks, and realized vs benchmark rate "
        "by partner."
    ),
    "no_forecasting": (
        "I report on historical data only, so I don't forecast or project future numbers. "
        "I can show you the recent trend over the periods we have instead."
    ),
    "in_scope_only": (
        "That's outside what I can answer. I only cover affiliate-partnerships analytics over "
        "the governed views — revenue, clicks, cost, benchmarks, email, and so on."
    ),
}


class SqlSafetyError(Exception):
    pass


class LLM(Protocol):
    def json(self, system: str, user: str) -> dict: ...
    def text(self, system: str, user: str) -> str: ...


@dataclass
class ScriptedLLM:
    """Replays queued responses in order. dict -> returned by json(); str -> by text()."""
    responses: list
    calls: list = field(default_factory=list)

    def _next(self):
        if not self.responses:
            raise AssertionError("ScriptedLLM ran out of responses")
        return self.responses.pop(0)

    def json(self, system: str, user: str) -> dict:
        self.calls.append(("json", user))
        r = self._next()
        return r if isinstance(r, dict) else json.loads(r)

    def text(self, system: str, user: str) -> str:
        self.calls.append(("text", user))
        return str(self._next())


class ClaudeLLM:
    """Live Claude client (Haiku). Cheap, fast; thinking left adaptive, text blocks extracted."""

    def __init__(self, api_key: str | None = None, model: str = MODEL, max_tokens: int = 1024):
        import anthropic
        self._client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
        self.model = model
        self.max_tokens = max_tokens

    def _complete(self, system: str, user: str) -> str:
        msg = self._client.messages.create(
            model=self.model, max_tokens=self.max_tokens, system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(b.text for b in msg.content if getattr(b, "type", None) == "text").strip()

    def json(self, system: str, user: str) -> dict:
        return _parse_json(self._complete(system, user))

    def text(self, system: str, user: str) -> str:
        return self._complete(system, user)


def _parse_json(text: str) -> dict:
    """Tolerant JSON extraction: strip fences, take the outermost object."""
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\n?|\n?```$", "", t).strip()
    start, end = t.find("{"), t.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"No JSON object in model response: {text[:200]!r}")
    return json.loads(t[start:end + 1])


def validate_sql(sql: str, known_views: set[str]) -> None:
    """Read-only guard: SELECT/WITH only, no DDL/DML, must reference a known view."""
    s = sql.strip().rstrip(";").strip()
    if not re.match(r"(?is)^\s*(select|with)\b", s):
        raise SqlSafetyError("Only SELECT/WITH queries are allowed.")
    if ";" in s:
        raise SqlSafetyError("Multiple statements are not allowed.")
    if _FORBIDDEN.search(s):
        raise SqlSafetyError("Query contains a forbidden keyword.")
    if not any(re.search(rf"\b{v}\b", s) for v in known_views):
        raise SqlSafetyError("Query does not reference any known view.")


@dataclass
class AgentResult:
    question: str
    refused: bool = False
    refusal_reason: str | None = None
    answer: str = ""
    plan: str | None = None
    sql: str | None = None
    caveat: str | None = None
    columns: list | None = None
    rows: list | None = None
    trace: list = field(default_factory=list)


_SYSTEM_TMPL = """You are the Affiliate Partnerships Analyst, a careful analytics agent for a \
consumer-savings affiliate business. You answer questions by writing DuckDB SQL against a \
governed semantic layer and reconciling to trusted numbers. You EXPLAIN and reconcile — you \
never recommend actions, and you report on historical data only.

{layer}
"""

_PLAN_TMPL = """Question: {q}

Decide if this is in scope. Set in_scope=false with the matching refusal_reason if it asks for \
a recommendation or action (explain_not_recommend), a forecast/prediction/projection \
(no_forecasting), or anything not answerable from these views (in_scope_only).

Otherwise write ONE DuckDB query (SELECT or WITH only) that answers it and obeys EVERY guardrail: \
apply the default property yourself when none is named; if a property is named, filter to it; if \
asked 'by property', group by it. Aggregate each source independently in its own CTE and never \
row-join two fact tables then subtract/divide. Filter both sides of any ratio. Compute in SQL.

Respond with ONLY this JSON (no prose, no code fences):
{{"in_scope": true/false, "refusal_reason": "<id or null>", "plan": "1-3 sentences: views, grain, \
filters, which guardrails apply", "sql": "<query or null>", "caveat": "<short note or null>"}}"""

_NARRATE_TMPL = """Question: {q}
Plan: {plan}
Result columns: {cols}
Result rows: {rows}
{caveat}
Write a concise answer (1-3 sentences). State the number(s) with units ($ or %), and name the \
property used. Do not invent numbers beyond the result. If a caveat is given, add one short \
sentence. Plain text only."""


class PartnershipsAgent:
    def __init__(self, con, llm: LLM, sem: dict | None = None, max_rows: int = 200):
        self.con = con
        self.llm = llm
        self.sem = sem or load_semantic()
        self.system = _SYSTEM_TMPL.format(layer=render_layer(self.sem))
        self.views = view_names(self.sem)
        self.max_rows = max_rows

    def answer(self, question: str) -> AgentResult:
        res = AgentResult(question=question)
        plan = self.llm.json(self.system, _PLAN_TMPL.format(q=question))

        if not plan.get("in_scope", True) or plan.get("refusal_reason"):
            reason = plan.get("refusal_reason") or "in_scope_only"
            res.refused = True
            res.refusal_reason = reason
            res.answer = REFUSAL_TEXT.get(reason, REFUSAL_TEXT["in_scope_only"])
            res.trace = [f"Scope check: refused ({reason})."]
            return res

        sql = (plan.get("sql") or "").strip()
        validate_sql(sql, self.views)
        res.plan = plan.get("plan")
        res.sql = sql
        res.caveat = plan.get("caveat")

        cur = self.con.execute(sql)
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()[: self.max_rows]
        res.columns = cols
        res.rows = rows

        res.trace = [
            "Scope check: in scope.",
            f"Plan: {res.plan}",
            *_guardrail_notes(sql),
            f"SQL:\n{sql}",
            f"Executed: {len(rows)} row(s) returned.",
        ]

        narrate = _NARRATE_TMPL.format(
            q=question, plan=res.plan, cols=cols, rows=rows[:20],
            caveat=(f"Caveat: {res.caveat}" if res.caveat else ""),
        )
        res.answer = self.llm.text(self.system, narrate)
        return res


def _guardrail_notes(sql: str) -> list[str]:
    s = sql.lower()
    notes = []
    if "property = 'us_main'" in s or 'property="us_main"' in s:
        notes.append("Guardrail: applied the named default property (us_main).")
    if re.search(r"group\s+by\s+property", s):
        notes.append("Guardrail: grouped by property (no single-property filter).")
    if s.count("with ") >= 1 and s.count(" as (") >= 2:
        notes.append("Guardrail: aggregated each source independently, then combined.")
    return notes
