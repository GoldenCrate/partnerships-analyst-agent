# -*- coding: utf-8 -*-
"""Load the governed semantic layer and render it into the agent's system prompt."""
from __future__ import annotations

import os
import yaml

_HERE = os.path.dirname(__file__)
SEMANTIC_PATH = os.path.join(_HERE, "semantic.yaml")


def load_semantic(path: str = SEMANTIC_PATH) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def render_layer(sem: dict) -> str:
    """Render the semantic layer as compact text for the system prompt."""
    lines: list[str] = []
    lines.append(f"REFERENCE DATE (treat as 'today'): {sem['reference_date']}")
    lines.append(f"DEFAULT PROPERTY: {sem['default_property']}")
    lines.append("")
    lines.append("GLOSSARY:")
    for k, v in sem["glossary"].items():
        lines.append(f"  - {k}: {v}")
    lines.append("")
    lines.append("GUARDRAILS (follow all):")
    for g in sem["guardrails"]:
        lines.append(f"  - [{g['id']}] {g['rule']}")
    lines.append("")
    lines.append("VIEWS (write SQL only against these):")
    for name, v in sem["views"].items():
        cols = ", ".join(f"{c} ({d})" for c, d in v["columns"].items())
        lines.append(f"  {name}  — grain: {v['grain']}")
        lines.append(f"     {v['description']}")
        lines.append(f"     columns: {cols}")
    lines.append("")
    lines.append("METRIC DEFINITIONS:")
    for m, d in sem["metrics"].items():
        lines.append(f"  - {m} = {d}")
    return "\n".join(lines)


def view_names(sem: dict) -> set[str]:
    return set(sem["views"].keys())
