# -*- coding: utf-8 -*-
"""Golden questions: the reconciliation harness.

Each answerable question has canonical SQL (the 'trusted dashboard' definition) written to
obey the guardrails — default property applied explicitly, cost and clicks aggregated
independently, filters on both sides of every ratio. The expected numbers are locked in so
the tests tie out and catch any drift. Refusal questions assert the agent declines for the
right reason.

At runtime the live agent is compared against these same canonical answers.
"""
from __future__ import annotations

# Date windows resolved from the dataset's reference date (2026-10-31).
W30 = "date BETWEEN DATE '2026-10-02' AND DATE '2026-10-31'"      # last 30 days
THIS_MONTH = "date BETWEEN DATE '2026-10-01' AND DATE '2026-10-31'"
LAST_MONTH = "date BETWEEN DATE '2026-09-01' AND DATE '2026-09-30'"

ANSWERABLE = [
    {
        "id": "gross_profit_30d_default",
        "question": "gross profit last 30 days",
        "note": "no property named -> default us_main; gross profit = commission - revenue share cost",
        "sql": f"""
            SELECT ROUND(SUM(commission) - SUM(revenue_share_cost), 2) AS true_gross_profit
            FROM REVENUE_SUMMARY
            WHERE property = 'us_main' AND {W30}
        """,
    },
    {
        "id": "gross_profit_30d_uk",
        "question": "gross profit last 30 days on the uk site",
        "note": "property named -> filter uk_site",
        "sql": f"""
            SELECT ROUND(SUM(commission) - SUM(revenue_share_cost), 2) AS true_gross_profit
            FROM REVENUE_SUMMARY
            WHERE property = 'uk_site' AND {W30}
        """,
    },
    {
        "id": "gross_profit_by_property_30d",
        "question": "gross profit by property last 30 days",
        "note": "'by property' -> group, do not filter",
        "sql": f"""
            SELECT property, ROUND(SUM(commission) - SUM(revenue_share_cost), 2) AS true_gross_profit
            FROM REVENUE_SUMMARY
            WHERE {W30}
            GROUP BY property ORDER BY true_gross_profit DESC
        """,
    },
    {
        "id": "top_merchants_commission_30d",
        "question": "top 5 merchants by commission last 30 days",
        "note": "default property",
        "sql": f"""
            SELECT merchant_domain, ROUND(SUM(commission), 2) AS commission
            FROM REVENUE_SUMMARY
            WHERE property = 'us_main' AND {W30}
            GROUP BY merchant_domain ORDER BY commission DESC LIMIT 5
        """,
    },
    {
        "id": "aov_30d_default",
        "question": "average order value last 30 days",
        "note": "AOV from transactional view, default property",
        "sql": f"""
            SELECT ROUND(AVG(order_amount), 2) AS aov
            FROM REVENUE_TRANSACTIONS
            WHERE property = 'us_main' AND {W30}
        """,
    },
    {
        "id": "ctor_30d_default",
        "question": "email CTOR last 30 days",
        "note": "CTOR = email_clicks / email_opens, default property",
        "sql": f"""
            SELECT ROUND(SUM(email_clicks) * 1.0 / SUM(email_opens), 4) AS ctor
            FROM EMAIL_PERFORMANCE
            WHERE property = 'us_main' AND {W30}
        """,
    },
    {
        "id": "ecpc_merchant_30d",
        "question": "eCPC for shopmart last 30 days",
        "note": "cost not pre-joined -> aggregate AD_SPEND and EFFECTIVE_CLICKS independently, divide; filter both sides",
        "sql": f"""
            WITH cost AS (
                SELECT SUM(ad_cost) AS c FROM AD_SPEND
                WHERE property = 'us_main' AND merchant_domain = 'shopmart.example' AND {W30}
            ), eff AS (
                SELECT SUM(effective_clicks) AS e FROM EFFECTIVE_CLICKS
                WHERE property = 'us_main' AND merchant_domain = 'shopmart.example' AND {W30}
            )
            SELECT ROUND(cost.c / eff.e, 4) AS ecpc FROM cost, eff
        """,
    },
    {
        "id": "cr_merchant_30d",
        "question": "conversion rate for shopmart last 30 days",
        "note": "CR = orders / clicks, aggregated independently, filter both sides",
        "sql": f"""
            WITH o AS (
                SELECT SUM(orders) AS orders FROM REVENUE_SUMMARY
                WHERE property = 'us_main' AND merchant_domain = 'shopmart.example' AND {W30}
            ), c AS (
                SELECT SUM(clicks) AS clicks FROM CLICKS
                WHERE property = 'us_main' AND merchant_domain = 'shopmart.example' AND {W30}
            )
            SELECT ROUND(o.orders * 1.0 / c.clicks, 4) AS conversion_rate FROM o, c
        """,
    },
    {
        "id": "epc_by_partner_30d",
        "question": "EPC by partner last 30 days",
        "note": "EPC = commission / clicks per partner, aggregated independently then joined on partner",
        "sql": f"""
            WITH comm AS (
                SELECT partner, SUM(commission) AS commission FROM REVENUE_SUMMARY
                WHERE property = 'us_main' AND {W30} GROUP BY partner
            ), clk AS (
                SELECT partner, SUM(clicks) AS clicks FROM CLICKS
                WHERE property = 'us_main' AND {W30} GROUP BY partner
            )
            SELECT comm.partner, ROUND(comm.commission / clk.clicks, 4) AS epc
            FROM comm JOIN clk USING (partner) ORDER BY epc DESC
        """,
    },
    {
        "id": "realized_vs_benchmark_shopmart",
        "question": "realized vs benchmark rate for shopmart",
        "note": "realized = commission/order_amount; join benchmarks on (partner, merchant_domain)",
        "sql": """
            WITH realized AS (
                SELECT partner, merchant_domain,
                       SUM(commission) / NULLIF(SUM(order_amount), 0) AS realized_rate
                FROM REVENUE_SUMMARY GROUP BY partner, merchant_domain
            )
            SELECT b.partner, b.benchmark_rate, ROUND(r.realized_rate, 4) AS realized_rate
            FROM RATE_BENCHMARKS b JOIN realized r USING (partner, merchant_domain)
            WHERE b.merchant_domain = 'shopmart.example' ORDER BY b.partner
        """,
    },
    {
        "id": "revenue_this_vs_last_month",
        "question": "commission this month vs last month",
        "note": "comparison question -> two windows and a stated delta, default property",
        "sql": f"""
            WITH t AS (
                SELECT SUM(commission) AS v FROM REVENUE_SUMMARY
                WHERE property = 'us_main' AND {THIS_MONTH}
            ), l AS (
                SELECT SUM(commission) AS v FROM REVENUE_SUMMARY
                WHERE property = 'us_main' AND {LAST_MONTH}
            )
            SELECT ROUND(t.v, 2) AS this_month, ROUND(l.v, 2) AS last_month,
                   ROUND(t.v - l.v, 2) AS delta FROM t, l
        """,
    },
    {
        "id": "cashback_orders_30d_default",
        "question": "orders from a cashback click last 30 days",
        "note": "looks-simple-isn't: answer cashback orders (is_cashback=true); note it won't reconcile to cashback clicks/conversion",
        "sql": f"""
            SELECT SUM(CASE WHEN is_cashback THEN 1 ELSE 0 END) AS cashback_orders
            FROM REVENUE_TRANSACTIONS
            WHERE property = 'us_main' AND {W30}
        """,
    },
]

REFUSALS = [
    {"id": "which_partners_to_drop", "question": "which partners should we drop?",
     "reason": "explain_not_recommend"},
    {"id": "forecast_december", "question": "what will December revenue be?",
     "reason": "no_forecasting"},
    {"id": "out_of_scope_weather", "question": "what's the weather in San Francisco?",
     "reason": "in_scope_only"},
]
