"""Lightweight page-view analytics — a thin sqlite store plus a pure aggregation. Recording
never raises (analytics must not break a page render). Only the admin sees the results."""

import os
import pathlib
import re
import sqlite3
import time
from collections import Counter

DB_PATH = pathlib.Path(__file__).parent / "data" / "analytics.db"


def _conn(path=None) -> sqlite3.Connection:
    p = pathlib.Path(path or os.environ.get("ANALYTICS_DB") or DB_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p)
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE IF NOT EXISTS views ("
                 "id INTEGER PRIMARY KEY, ts REAL, path TEXT, discord_id TEXT)")
    return conn


def record(path: str, discord_id=None, dbpath=None) -> None:
    """Log one page view. Swallows all errors — a page render must never fail on analytics."""
    try:
        with _conn(dbpath) as conn:
            conn.execute("INSERT INTO views (ts, path, discord_id) VALUES (?, ?, ?)",
                         (time.time(), path, str(discord_id) if discord_id else None))
    except Exception:  # analytics must never break a page render
        pass


def norm_path(p: str) -> str:
    """Collapse dynamic ids so top-pages aggregates by feature, not individual item."""
    p = re.sub(r"/(player|team|game)/[^/]+", r"/\1/:id", p)
    p = re.sub(r"/matchup/[^/]+/[^/]+", "/matchup/:wk/:id", p)
    return p or "/"


def summary(name_of: dict | None = None, recent_n: int = 30, dbpath=None) -> dict:
    """Aggregate views into overview stats, top pages, per-visitor activity, and a recent feed.
    `name_of` maps a (string) discord_id to a display name."""
    name_of = name_of or {}
    with _conn(dbpath) as conn:
        rows = conn.execute("SELECT ts, path, discord_id FROM views ORDER BY ts").fetchall()

    total = len(rows)
    anon = sum(1 for r in rows if not r["discord_id"])
    pages = [{"path": p, "views": n} for p, n in Counter(norm_path(r["path"]) for r in rows).most_common(12)]

    vis: dict = {}
    for r in rows:
        d = r["discord_id"]
        if not d:
            continue
        v = vis.setdefault(d, {"views": 0, "last": 0.0})
        v["views"] += 1
        v["last"] = max(v["last"], r["ts"])
    visitors = sorted(
        ({"name": name_of.get(d, d), "views": v["views"], "last": v["last"]} for d, v in vis.items()),
        key=lambda x: x["views"], reverse=True)

    recent = [{"ts": r["ts"], "path": r["path"],
               "who": name_of.get(r["discord_id"], "anon" if not r["discord_id"] else r["discord_id"])}
              for r in reversed(rows)][:recent_n]

    return {"total": total, "unique": len(vis), "anon": anon, "pages": pages,
            "visitors": visitors, "recent": recent, "since": rows[0]["ts"] if rows else None}
