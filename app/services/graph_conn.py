"""Graph-serving connection factory (DEV-007R-HOTFIX-2).

app/graphs must not import app.deps (W1 layering rule), but the compiled
LangGraph still needs a live DB connection for capability execution and
conversation-meta synthesis. This module holds that platform dependency so
the layering rule stays enforced.
"""
from __future__ import annotations


def default_conn():
    from app import deps
    from app.database.sqlite import connect

    return connect(str(deps.DB_PATH))
