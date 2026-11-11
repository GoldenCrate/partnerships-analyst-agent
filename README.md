# Affiliate Partnerships Analyst

A natural-language analytics agent for affiliate partnerships. Ask a plain-English question
and it writes DuckDB SQL against a **governed semantic layer**, runs it, and shows you the
answer, the table behind it, the SQL it wrote, and a short reasoning trace. It **explains and
reconciles — it does not recommend actions**, and it refuses out-of-scope questions.

**Live demo:** https://partnerships-analyst-agent.streamlit.app

> Everything here is **synthetic and de-branded**. It demonstrates a pattern (a governed
> text-to-SQL agent) with invented data — no proprietary code, data, object names, or numbers.

## Why it's interesting

The hard part of an analytics agent isn't the chat box — it's governance and arithmetic
integrity. This agent is built around the mistakes that actually break these numbers:

- **`property` is a first-class dimension with a named default.** Most questions implicitly
  mean one surface; the agent applies the default (`us_main`) explicitly, because naming a
  default in the layer does *not* make it apply to generated SQL.
- **Cost lives at a different grain from revenue.** The agent never row-joins two fact tables
  and subtracts; it pre-aggregates each source independently, then combines.
- **A filter applies to both sides of a ratio** (e.g. eCPC = ad_cost / effective_clicks).
- **It computes in SQL, not in prose**, and surfaces each guardrail in the trace.
- **It reconciles to trusted numbers.** A set of golden questions has canonical SQL and locked
  expected answers; the test suite proves they tie out.

## How it works

```
question → scope-check → plan → text-to-SQL → execute (read-only) → narrate
                                   │
                        governed semantic layer (semantic.yaml)
                                   │
                              DuckDB warehouse
```

- `partnerships_agent/data_gen.py` — builds the synthetic warehouse (10 governed views) in
  DuckDB, deterministically, on launch.
- `partnerships_agent/semantic.yaml` — the governed layer: views, grains, metric formulas,
  and the guardrails the agent must follow.
- `partnerships_agent/agent.py` — the agent loop and a small LLM seam (`ClaudeLLM` for live,
  `ScriptedLLM` for tests). SQL is validated read-only before it runs.
- `partnerships_agent/golden.py` — golden questions: canonical SQL + locked trusted answers,
  plus questions the agent must refuse.
- `app.py` — the Streamlit chat UI.

The live app uses **Claude Haiku** via an API key; the test suite uses a scripted mock, so it
runs without any key.

## Run it locally

```bash
pip install -r requirements-dev.txt
pytest -q                                   # 22 tests, no API key needed
export ANTHROPIC_API_KEY=sk-ant-...         # for live answers (Haiku; pennies)
streamlit run app.py
```

Without a key the app runs in **offline mode**: the example questions still show the trusted
canonical answers. With a key, you can ask your own questions and they run live.

## Deploy (Streamlit Community Cloud)

Point a new app at this repo (`app.py`), then add your key under **Settings → Secrets**:

```toml
ANTHROPIC_API_KEY = "sk-ant-..."
```

The model is Claude Haiku with short token caps and a per-session question limit, so a demo
costs well under a dollar.

## Tests

```
pytest -q
```

Covers: every golden question ties out to its trusted number, the dataset rebuilds
identically, the two-grain revenue views reconcile, the agent pipeline executes grounded SQL
and surfaces guardrails, all refusals fire, and unsafe SQL is blocked.
