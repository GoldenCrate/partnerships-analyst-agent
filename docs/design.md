# Affiliate Partnerships Analyst — design

A de-branded, synthetic-data demonstration of a **natural-language-to-SQL analytics agent
over a governed semantic layer**. Ask a plain-English question; get the number, the table
behind it, the SQL the agent wrote, and a short reasoning trace. It **explains and
reconciles — it does not recommend actions**, and it visibly refuses questions outside its
scope.

This mirrors a production agent (built on Snowflake Cortex) in *pattern and vocabulary
only*. No proprietary code, data, object names, or numbers — everything here is synthetic.

## Why this exists
A portfolio piece for a Data Analyst role. The point it proves to a non-technical
executive: an AI agent can answer partnerships questions in plain English **and reconcile
to the numbers the team already trusts**, because the hard part is governance and
arithmetic integrity, not the chat box.

## Intent / goals
- A live link anyone can open and type their own partnerships question into, and it runs.
- Answers tie out to pre-computed "trusted" numbers on a set of golden questions.
- The reasoning trace shows the guardrails doing real work.

## Non-goals / anti-goals
- No action recommendations ("which partners should we drop") — it refuses.
- No forecasting / prediction — it refuses.
- Not a general chatbot: out-of-scope questions are declined, not guessed.
- No real/confidential data or object names. Ever.

## The domain (synthetic)
Affiliate partnerships for a consumer savings site + browser extension. Two counterparties
that get conflated: the **partner** (the network that reports the sale and pays) and the
**merchant** (the retailer the shopper buys from). The same merchant is reachable through
several partners at different rates. Metric chain: traffic in → clicks out → orders →
commission → cost → gross profit.

**property** is the native term and the #1 source of number disagreements: several consumer
surfaces (regional sites, the extension, a secondary brand). Nearly every question
implicitly means one. There is a **named default property** (`us_main`).

## Governed views (the semantic layer)
`REVENUE_SUMMARY`, `REVENUE_TRANSACTIONS`, `AD_SPEND`, `CLICKS`, `EFFECTIVE_CLICKS`,
`PAGE_VIEWS`, `TRAFFIC_ANALYTICS`, `SEARCH_PERFORMANCE`, `RATE_BENCHMARKS`,
`EMAIL_PERFORMANCE`. Four structural realism details are deliberate:
1. a **named default** (`property = us_main`) that must be applied explicitly — naming it in
   the layer does not auto-apply it to generated SQL;
2. **two views at different grain over the same facts** (`REVENUE_SUMMARY` aggregate vs
   `REVENUE_TRANSACTIONS` transactional);
3. joins happen **on merchant domain**;
4. **cost is not pre-joined to revenue** (`AD_SPEND` sits at a different grain) — it must be
   aggregated independently and then combined.

## Guardrails (surfaced in the trace)
- **Pre-aggregate each source independently, then combine. Never join two fact tables
  row-by-row and then subtract.**
- **A filter applies to both sides of a ratio.**
- **A named filter in the layer does not auto-apply** to generated SQL.
- **Compute in SQL, not in prose.**
- **true gross profit = commission − revenue share cost.**
- Scope check + honest refusal; the three cashback terms (cashback click / cashback order /
  cashback conversion) deliberately **do not reconcile**, and the agent says so.

## Architecture
Python. Claude (**Haiku 5.5**) does scope-check → plan → text-to-SQL → reflect → format.
DuckDB is the warehouse; the semantic layer is a small YAML the agent reads. Streamlit chat
UI shows answer + table + SQL + trace. An LLM-client seam lets tests run with a scripted
mock (no API key); the deployed app uses a real key from Streamlit secrets with a cheap
model, short token caps, and a per-session question limit.

## Reconciliation harness
~12 golden questions with canonical SQL and pre-computed trusted answers (plus 2 the agent
must refuse). Tests prove the canonical SQL ties to the trusted numbers and that the agent
pipeline + guardrails behave. At runtime the live agent is compared against these.

## "Today"
The dataset is anchored to a fixed reference date (its max date), so "last 30 days" and
"this month" are stable in the demo regardless of the real calendar.
