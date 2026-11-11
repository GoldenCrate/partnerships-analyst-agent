# -*- coding: utf-8 -*-
"""Reconciliation tests: the canonical SQL for every golden question must tie out to the
locked 'trusted' numbers, and the dataset must rebuild identically. No API key needed."""
import pytest

from partnerships_agent.data_gen import build_database
from partnerships_agent.golden import ANSWERABLE

# The trusted numbers. If data generation drifts, these fail — that's the point.
EXPECTED = {
    "gross_profit_30d_default": [(2952.37,)],
    "gross_profit_30d_uk": [(1471.32,)],
    "gross_profit_by_property_30d": [
        ("us_main", 2952.37), ("extension", 1993.18),
        ("uk_site", 1471.32), ("secondary_brand", 668.16),
    ],
    "top_merchants_commission_30d": [
        ("beautique.example", 663.56), ("glowbar.example", 466.34),
        ("dealcrate.example", 301.66), ("fitthread.example", 266.11),
        ("megadeal.example", 252.12),
    ],
    "aov_30d_default": [(197.69,)],
    "ctor_30d_default": [(0.1442,)],
    "ecpc_merchant_30d": [(0.5787,)],
    "cr_merchant_30d": [(0.0153,)],
    "epc_by_partner_30d": [
        ("partner_alpha", 0.2876), ("partner_beta", 0.214), ("partner_delta", 0.1967),
        ("partner_epsilon", 0.1898), ("partner_gamma", 0.1366),
    ],
    "realized_vs_benchmark_shopmart": [("partner_delta", 0.0414, 0.0424)],
    "revenue_this_vs_last_month": [(4805.41, 4201.33, 604.07)],
    "impressions_30d_default": [(130442,)],
    "ctr_merchant_30d": [(0.0114,)],
    "ecpm_30d_default": [(5.4128,)],
    "rpm_30d_default": [(12.15,)],
    "cashback_orders_30d_default": [(170,)],
}


@pytest.fixture(scope="module")
def con():
    return build_database()


@pytest.mark.parametrize("q", ANSWERABLE, ids=lambda q: q["id"])
def test_golden_question_ties_out(con, q):
    rows = con.execute(q["sql"]).fetchall()
    assert rows == EXPECTED[q["id"]], f"{q['id']} drifted from trusted numbers"


def test_every_answerable_has_expected():
    assert {q["id"] for q in ANSWERABLE} == set(EXPECTED), "golden set and EXPECTED out of sync"


def test_dataset_is_deterministic():
    a = build_database().execute(
        "SELECT ROUND(SUM(commission) - SUM(revenue_share_cost), 2) FROM REVENUE_SUMMARY"
    ).fetchone()
    b = build_database().execute(
        "SELECT ROUND(SUM(commission) - SUM(revenue_share_cost), 2) FROM REVENUE_SUMMARY"
    ).fetchone()
    assert a == b


def test_summary_and_transactions_reconcile():
    """The two-grain views must agree on total commission (same facts, different grain)."""
    c = build_database()
    s = c.execute("SELECT ROUND(SUM(commission), 2) FROM REVENUE_SUMMARY").fetchone()[0]
    t = c.execute("SELECT ROUND(SUM(commission), 2) FROM REVENUE_TRANSACTIONS").fetchone()[0]
    assert s == t
