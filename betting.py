"""Play-money betting on your own weekly matchup (flag: cfg.enable_betting).

Split by design: the top half is PURE (plain dicts/lists, unit-testable, no I/O)
and the bottom half is a thin sqlite3 store. A logged-in member wagers 'chips'
that their own roster WINS its head-to-head that week. Even money: win → +stake,
loss → -stake, tie → push (0). Everyone starts at STARTING_BALANCE.
"""

import os
import pathlib
import sqlite3
import time

# --- pure logic -----------------------------------------------------------
STARTING_BALANCE = 1000


def _pair_by_matchup(week_matchups: list[dict]) -> dict:
    """Group a raw Sleeper week array into head-to-heads by matchup_id (same
    pairing views.scoreboard uses — kept tiny and local, no shaping duplicated)."""
    groups: dict = {}
    for m in week_matchups:
        groups.setdefault(m.get("matchup_id"), []).append(m)
    return groups


def matchup_result(week_matchups: list[dict], roster_id: int) -> str | None:
    """'win' | 'loss' | 'tie' for roster_id's head-to-head that week, or None when
    the roster isn't in the week, has no 2-side matchup (bye), or the game hasn't
    been played yet (both sides still at 0)."""
    for entries in _pair_by_matchup(week_matchups).values():
        ids = [e.get("roster_id") for e in entries]
        if roster_id not in ids or len(entries) != 2:
            continue
        mine = next(e for e in entries if e.get("roster_id") == roster_id)
        opp = next(e for e in entries if e.get("roster_id") != roster_id)
        pm, po = mine.get("points") or 0, opp.get("points") or 0
        if pm == 0 and po == 0:
            return None  # not yet played
        if pm > po:
            return "win"
        if pm < po:
            return "loss"
        return "tie"
    return None


def settle(bets: list[dict], results: dict) -> list[dict]:
    """Attach payout/status to each bet. results maps (roster_id, week) ->
    'win'|'loss'|'tie'; a bet with no result yet stays 'open' (payout 0)."""
    out = []
    for b in bets:
        res = results.get((b["roster_id"], b["week"]))
        stake = b["stake"]
        if res == "win":
            payout, status = stake, "won"
        elif res == "loss":
            payout, status = -stake, "lost"
        elif res == "tie":
            payout, status = 0, "push"
        else:
            payout, status = 0, "open"
        out.append({**b, "payout": payout, "status": status})
    return out


def balances(settled_bets: list[dict]) -> dict:
    """roster_id -> STARTING_BALANCE + sum of that roster's settled payouts."""
    bal: dict = {}
    for b in settled_bets:
        bal.setdefault(b["roster_id"], STARTING_BALANCE)
        bal[b["roster_id"]] += b["payout"]
    return bal


# --- thin sqlite store -----------------------------------------------------
DB_PATH = pathlib.Path(__file__).parent / "data" / "bets.db"


def _resolve(path=None) -> pathlib.Path:
    return pathlib.Path(path or os.environ.get("BETS_DB") or DB_PATH)


def _conn(path=None) -> sqlite3.Connection:
    p = _resolve(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p)
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE IF NOT EXISTS bets ("
        "id INTEGER PRIMARY KEY, roster_id INT, week INT, stake INT, created REAL, "
        "UNIQUE(roster_id, week))"
    )
    return conn


def place_bet(roster_id: int, week: int, stake: int, path=None) -> None:
    """Record one wager. Rejects a non-positive/non-int stake, or a second bet for
    the same roster+week (one open bet per week per member, enforced by UNIQUE)."""
    if isinstance(stake, bool) or not isinstance(stake, int) or stake <= 0:
        raise ValueError("stake must be a positive integer")
    with _conn(path) as conn:
        try:
            conn.execute(
                "INSERT INTO bets (roster_id, week, stake, created) VALUES (?, ?, ?, ?)",
                (int(roster_id), int(week), stake, time.time()),
            )
        except sqlite3.IntegrityError:
            raise ValueError("a bet already exists for this roster this week")


def all_bets(path=None) -> list[dict]:
    with _conn(path) as conn:
        rows = conn.execute(
            "SELECT id, roster_id, week, stake, created FROM bets ORDER BY created DESC"
        ).fetchall()
    return [dict(r) for r in rows]
