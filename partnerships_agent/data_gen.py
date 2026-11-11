# -*- coding: utf-8 -*-
"""Synthetic affiliate-partnerships dataset for the Partnerships Analyst agent.

Everything here is invented and seeded, so the whole warehouse rebuilds identically on every
launch (no files to ship, no secrets). The shapes mirror a real partnerships semantic layer:
a partner (the network that reports and pays) vs a merchant (the retailer), the same merchant
reachable through several partners at different rates, a `property` dimension with a named
default, and cost that lives at a different grain from revenue so it can't be row-joined.

`build_database()` returns a DuckDB connection with the eleven governed base tables loaded.
"""
from __future__ import annotations

import datetime as dt
import math
import random
from dataclasses import dataclass

import duckdb
import pandas as pd

SEED = 42
# Bump whenever the schema changes (tables/columns). Deploys key their cached DB on this, so a
# change always rebuilds the warehouse instead of reusing a stale cached file.
SCHEMA_VERSION = 2
N_DAYS = 90
REFERENCE_DATE = dt.date(2026, 10, 31)  # the dataset's "today"; keeps relative dates stable
START_DATE = REFERENCE_DATE - dt.timedelta(days=N_DAYS - 1)

# property is the native dimension and the #1 source of number disagreements.
PROPERTIES = ["us_main", "uk_site", "extension", "secondary_brand"]
DEFAULT_PROPERTY = "us_main"
PROPERTY_WEIGHT = {"us_main": 1.0, "uk_site": 0.5, "extension": 0.7, "secondary_brand": 0.28}

# The network that reports the sale and pays us.
PARTNERS = ["partner_alpha", "partner_beta", "partner_gamma", "partner_delta", "partner_epsilon"]

# commission models the layer knows about
COMMISSION_MODELS = ["CPA", "CPC", "CPL", "flat_fee"]

CATEGORIES = {
    # category -> (avg order value, baseline commission rate)
    "electronics": (180.0, 0.040),
    "apparel": (70.0, 0.090),
    "home": (110.0, 0.065),
    "beauty": (45.0, 0.100),
    "travel": (420.0, 0.030),
    "marketplace": (95.0, 0.050),
}

MERCHANT_NAMES = [
    "shopmart", "techzone", "stylehaus", "homenest", "glowbar", "wanderly", "megadeal",
    "pixelgear", "urbanthread", "coziliving", "pureglow", "jetset", "bargainbay", "voltix",
    "fitthread", "nestwell", "lumiglow", "roamly", "dealcrate", "gadgetry", "weardrobe",
    "casae", "skincraft", "voyagr", "savemore", "circuitco", "trendloom", "hearthly",
    "beautique", "globetrek",
]


@dataclass
class MerchantLink:
    merchant_domain: str
    category: str
    partner: str
    commission_model: str
    benchmark_rate: float   # the published/expected commission rate for this pair
    realized_rate: float    # what we actually earn (drifts from benchmark, fixed per pair)
    monetization: float     # effective-clicks / clicks for this pair
    cvr: float              # orders / effective-click for this pair
    cashback_prop: float    # P(order is a cashback order)


def _rng() -> random.Random:
    return random.Random(SEED)


def _build_merchants(rng: random.Random):
    merchants = []
    for i, name in enumerate(MERCHANT_NAMES):
        category = list(CATEGORIES)[i % len(CATEGORIES)]
        merchants.append({
            "merchant_domain": f"{name}.example",
            "category": category,
            "popularity": rng.uniform(0.3, 1.0),
        })
    return merchants


def _build_links(rng: random.Random, merchants) -> list[MerchantLink]:
    links: list[MerchantLink] = []
    for m in merchants:
        aov, base_rate = CATEGORIES[m["category"]]
        n_partners = rng.choice([1, 2, 2, 3])  # most merchants reachable via 2 partners
        chosen = rng.sample(PARTNERS, n_partners)
        for p in chosen:
            benchmark = base_rate * rng.uniform(0.9, 1.15)
            realized = benchmark * rng.uniform(0.82, 1.03)  # usually under benchmark
            links.append(MerchantLink(
                merchant_domain=m["merchant_domain"],
                category=m["category"],
                partner=p,
                commission_model=rng.choice(COMMISSION_MODELS),
                benchmark_rate=round(benchmark, 4),
                realized_rate=round(realized, 4),
                monetization=rng.uniform(0.35, 0.72),
                cvr=rng.uniform(0.02, 0.075),
                cashback_prop=rng.uniform(0.10, 0.55),
            ))
    return links


def _day_factor(d: dt.date, rng: random.Random) -> float:
    # mild weekly seasonality + gentle upward trend + noise
    weekday_boost = 1.12 if d.weekday() >= 5 else 1.0
    trend = 1.0 + 0.0015 * (d - START_DATE).days
    return weekday_boost * trend * rng.uniform(0.8, 1.2)


def _generate():
    rng = _rng()
    merchants = _build_merchants(rng)
    links = _build_links(rng, merchants)
    pop = {m["merchant_domain"]: m["popularity"] for m in merchants}
    aov_of = {m["merchant_domain"]: CATEGORIES[m["category"]][0] for m in merchants}

    clicks_rows, eff_rows, txn_rows = [], [], []
    # merchant/day/property accumulators for the independent tables
    clicks_by_mdp: dict[tuple, int] = {}

    dates = [START_DATE + dt.timedelta(days=i) for i in range(N_DAYS)]
    order_id = 0
    for d in dates:
        df = _day_factor(d, rng)
        for prop in PROPERTIES:
            pw = PROPERTY_WEIGHT[prop]
            for link in links:
                base = pop[link.merchant_domain] * pw * df
                clicks = max(0, int(round(base * rng.uniform(8, 30))))
                if clicks == 0:
                    continue
                effective = int(round(clicks * link.monetization))
                monetized = int(round(effective * rng.uniform(0.85, 0.98)))
                clicks_rows.append({
                    "date": d, "property": prop, "merchant_domain": link.merchant_domain,
                    "partner": link.partner, "clicks": clicks,
                })
                eff_rows.append({
                    "date": d, "property": prop, "merchant_domain": link.merchant_domain,
                    "partner": link.partner, "effective_clicks": effective,
                    "monetized_clicks": monetized,
                })
                key = (d, prop, link.merchant_domain)
                clicks_by_mdp[key] = clicks_by_mdp.get(key, 0) + clicks

                n_orders = 0
                for _ in range(effective):
                    if rng.random() < link.cvr:
                        n_orders += 1
                for _ in range(n_orders):
                    order_id += 1
                    amount = round(aov_of[link.merchant_domain] * rng.uniform(0.5, 1.9), 2)
                    commission = round(amount * link.realized_rate, 4)
                    is_cashback = rng.random() < link.cashback_prop
                    # revenue share cost = what we hand back to the user (cashback), else a small base
                    if is_cashback:
                        rev_share = round(amount * rng.uniform(0.02, 0.06), 4)
                    else:
                        rev_share = round(amount * rng.uniform(0.002, 0.008), 4)
                    txn_rows.append({
                        "order_id": order_id, "date": d, "property": prop,
                        "merchant_domain": link.merchant_domain, "partner": link.partner,
                        "order_amount": amount, "commission": commission,
                        "revenue_share_cost": rev_share, "is_cashback": is_cashback,
                    })

    txns = pd.DataFrame(txn_rows)
    clicks = pd.DataFrame(clicks_rows)
    eff = pd.DataFrame(eff_rows)

    # REVENUE_SUMMARY: aggregate grain over the SAME facts as REVENUE_TRANSACTIONS
    rev_summary = (
        txns.groupby(["date", "property", "partner", "merchant_domain"], as_index=False)
        .agg(orders=("order_id", "count"),
             commission=("commission", "sum"),
             revenue_share_cost=("revenue_share_cost", "sum"),
             order_amount=("order_amount", "sum"))
    )
    rev_summary["true_gross_profit"] = (
        rev_summary["commission"] - rev_summary["revenue_share_cost"]).round(4)

    # AD_SPEND: merchant/day/property grain (NO partner) -> cannot be row-joined to revenue
    rng2 = random.Random(SEED + 1)
    ad_rows = []
    for (d, prop, merch), clk in clicks_by_mdp.items():
        cpc = rng2.uniform(0.08, 0.35)
        ad_rows.append({"date": d, "property": prop, "merchant_domain": merch,
                        "ad_cost": round(clk * cpc, 2)})
    ad_spend = pd.DataFrame(ad_rows)

    # PAGE_VIEWS: merchant/day/property
    rng3 = random.Random(SEED + 2)
    pv_rows = []
    for (d, prop, merch), clk in clicks_by_mdp.items():
        pv_rows.append({"date": d, "property": prop, "merchant_domain": merch,
                        "page_views": int(round(clk * rng3.uniform(1.6, 3.4)))})
    page_views = pd.DataFrame(pv_rows)

    # AD_PERFORMANCE: on-site display/native ads at merchant/day/property grain.
    # Impressions scale off page views (several ad slots per page); ad_clicks via a small CTR;
    # ad_revenue via an effective CPM. This is what powers impressions, CTR, eCPM, and RPM.
    # ad_impressions here are AD impressions (distinct from SEARCH_PERFORMANCE.impressions),
    # and ad_clicks are ad-unit clicks (NOT merchant clicks) — a deliberate naming trap.
    rng7 = random.Random(SEED + 6)
    adperf_rows = []
    for r in pv_rows:
        pv = r["page_views"]
        impressions = int(round(pv * rng7.uniform(1.4, 3.1)))  # ad slots per page view
        if impressions <= 0:
            continue
        ad_clicks = int(round(impressions * rng7.uniform(0.004, 0.020)))  # display CTR
        ecpm = rng7.uniform(2.0, 9.0)  # effective revenue per 1,000 impressions ($)
        ad_revenue = round(impressions / 1000.0 * ecpm, 2)
        adperf_rows.append({"date": r["date"], "property": r["property"],
                            "merchant_domain": r["merchant_domain"],
                            "ad_impressions": impressions, "ad_clicks": ad_clicks,
                            "ad_revenue": ad_revenue})
    ad_performance = pd.DataFrame(adperf_rows)

    # TRAFFIC_ANALYTICS: property/day
    rng4 = random.Random(SEED + 3)
    traffic_rows = []
    daily_prop_clicks: dict[tuple, int] = {}
    for (d, prop, _m), clk in clicks_by_mdp.items():
        daily_prop_clicks[(d, prop)] = daily_prop_clicks.get((d, prop), 0) + clk
    for (d, prop), clk in daily_prop_clicks.items():
        sessions = int(round(clk * rng4.uniform(3.0, 5.0)))
        visitors = int(round(sessions * rng4.uniform(0.7, 0.9)))
        traffic_rows.append({"date": d, "property": prop, "sessions": sessions,
                             "visitors": visitors})
    traffic = pd.DataFrame(traffic_rows)

    # SEARCH_PERFORMANCE: merchant/day/property (search_clicks != merchant clicks)
    rng5 = random.Random(SEED + 4)
    search_rows = []
    for (d, prop, merch), clk in clicks_by_mdp.items():
        impressions = int(round(clk * rng5.uniform(12, 40)))
        search_clicks = int(round(impressions * rng5.uniform(0.02, 0.09)))
        search_rows.append({"date": d, "property": prop, "merchant_domain": merch,
                            "impressions": impressions, "search_clicks": search_clicks,
                            "avg_position": round(rng5.uniform(3.0, 22.0), 1)})
    search = pd.DataFrame(search_rows)

    # EMAIL_PERFORMANCE: property/day (email clicks != merchant clicks; CTOR = clicks/opens)
    rng6 = random.Random(SEED + 5)
    email_rows = []
    for (d, prop), clk in daily_prop_clicks.items():
        sent = int(round(clk * rng6.uniform(1.5, 3.5)))
        opens = int(round(sent * rng6.uniform(0.18, 0.32)))
        eclicks = int(round(opens * rng6.uniform(0.08, 0.20)))
        merch_clicks = int(round(eclicks * rng6.uniform(0.6, 0.9)))
        orders_email = int(round(merch_clicks * rng6.uniform(0.02, 0.06)))
        email_rows.append({"date": d, "property": prop, "emails_sent": sent,
                           "email_opens": opens, "email_clicks": eclicks,
                           "merchant_clicks": merch_clicks, "orders_from_email": orders_email})
    email = pd.DataFrame(email_rows)

    # RATE_BENCHMARKS: partner x merchant (join on merchant domain)
    rate_rows = [{
        "partner": l.partner, "merchant_domain": l.merchant_domain, "category": l.category,
        "commission_model": l.commission_model, "benchmark_rate": l.benchmark_rate,
    } for l in links]
    rate_benchmarks = pd.DataFrame(rate_rows)

    rev_txns = txns[["order_id", "date", "property", "merchant_domain", "partner",
                     "order_amount", "commission", "revenue_share_cost", "is_cashback"]]

    return {
        "REVENUE_SUMMARY": rev_summary,
        "REVENUE_TRANSACTIONS": rev_txns,
        "AD_SPEND": ad_spend,
        "CLICKS": clicks,
        "EFFECTIVE_CLICKS": eff,
        "PAGE_VIEWS": page_views,
        "AD_PERFORMANCE": ad_performance,
        "TRAFFIC_ANALYTICS": traffic,
        "SEARCH_PERFORMANCE": search,
        "RATE_BENCHMARKS": rate_benchmarks,
        "EMAIL_PERFORMANCE": email,
    }


def build_database(path: str = ":memory:") -> duckdb.DuckDBPyConnection:
    """Build the full synthetic warehouse and return a DuckDB connection."""
    tables = _generate()
    con = duckdb.connect(path)
    for name, frame in tables.items():
        con.register(f"_tmp_{name}", frame)
        con.execute(f"CREATE OR REPLACE TABLE {name} AS SELECT * FROM _tmp_{name}")
        con.unregister(f"_tmp_{name}")
    return con


if __name__ == "__main__":
    con = build_database()
    print(f"Reference date (dataset 'today'): {REFERENCE_DATE}")
    for name in _generate().keys():
        n = con.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
        print(f"  {name:22s} {n:>8,d} rows")
