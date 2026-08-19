"""Pure logic for the live matchup 'pulse' — rest-of-day projections and the event trigger.
No network, no discord. The bot resolves inputs (Sleeper matchups, projections, ESPN game
states) and calls these; keeping them pure makes the projection + trigger unit-testable."""


def roster_outlook(starters: list[str], starter_points: list[float], current_points: float,
                   proj_pts: dict, player_state: dict) -> dict:
    """Rest-of-day outlook for one roster.

    starters      : starter player ids
    starter_points: live points, parallel to starters
    current_points: the roster's live total (authoritative)
    proj_pts      : {pid: projected fantasy points}
    player_state  : {pid: 'pre'|'in'|'post'} from the player's NFL game

    Projected final = locked actuals for finished players + (higher of actual/projection) for
    in-progress + projection for not-yet-started. `remaining` = not-started starters by projection."""
    projected, remaining = 0.0, []
    for i, pid in enumerate(starters):
        actual = starter_points[i] if i < len(starter_points) else 0.0
        st = player_state.get(pid, "pre")
        proj = proj_pts.get(pid)
        if st == "post":
            projected += actual
        elif st == "in":
            projected += max(actual, proj if proj is not None else actual)
        else:  # pre — not started
            p = proj if proj is not None else 0.0
            projected += p
            if p > 0:
                remaining.append((pid, round(p, 1)))
    remaining.sort(key=lambda x: x[1], reverse=True)
    left = sum(1 for pid in starters if player_state.get(pid, "pre") != "post")
    return {"current": round(current_points, 1), "projected": round(projected, 1),
            "left": left, "remaining": remaining}


def should_post(final_now: int, total: int, posted_final: int) -> bool:
    """Post a pulse when a new batch of NFL games has gone final and games still remain —
    i.e. a slate just wrapped mid-week. All-final is left to the results announcer."""
    return posted_final < final_now < total
