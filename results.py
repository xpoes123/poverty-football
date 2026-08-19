"""Pure formatting for the weekly fantasy-results announcer. No network, no discord —
operates on the output of views.scoreboard (paired head-to-heads) so it's fully testable.
Factual only: winners, scores, and the week's high/low — no invented flavor."""


def result_lines(games: list[dict]) -> list[dict]:
    """Each decided matchup -> a result row, sorted by margin (blowouts first). `games` is
    views.scoreboard output: [{sides:[{team,points},...], winner: 0|1|None}]."""
    out = []
    for g in games:
        sides = g.get("sides") or []
        if len(sides) != 2:
            continue
        a, b = sides
        if a["points"] <= 0 and b["points"] <= 0:
            continue  # unplayed
        if g.get("winner") is None:  # equal, non-zero -> tie
            out.append({"tie": True, "a": a["team"], "b": b["team"], "pa": a["points"], "pb": b["points"]})
            continue
        w, ls = sides[g["winner"]], sides[1 - g["winner"]]
        out.append({"tie": False, "winner": w["team"], "loser": ls["team"],
                    "ws": w["points"], "ls": ls["points"], "margin": round(w["points"] - ls["points"], 1)})
    out.sort(key=lambda r: r.get("margin", -1), reverse=True)
    return out


def extremes(games: list[dict]) -> dict | None:
    """Highest- and lowest-scoring teams of the week (played teams only)."""
    teams = [(s["team"], s["points"]) for g in games for s in (g.get("sides") or []) if s["points"] > 0]
    if not teams:
        return None
    hi, lo = max(teams, key=lambda t: t[1]), min(teams, key=lambda t: t[1])
    return {"high_team": hi[0], "high": hi[1], "low_team": lo[0], "low": lo[1]}


def announcement(games: list[dict], week: int) -> dict | None:
    """Assemble the announcement (title, lines, extremes). None if nothing is final yet."""
    lines = result_lines(games)
    if not lines:
        return None
    return {"week": week, "lines": lines, "extremes": extremes(games)}
