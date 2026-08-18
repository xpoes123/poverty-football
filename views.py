"""Pure shaping of raw Sleeper payloads into view models. No network — unit-testable."""

CDN = "https://sleepercdn.com/avatars/thumbs"


def team_name(user: dict | None) -> str:
    if not user:
        return "Unclaimed"
    return (user.get("metadata") or {}).get("team_name") or user.get("display_name") or "—"


def avatar_url(user: dict | None) -> str | None:
    if not user:
        return None
    meta_av = (user.get("metadata") or {}).get("avatar")
    if meta_av:  # custom team avatar is a full URL
        return meta_av
    av = user.get("avatar")
    return f"{CDN}/{av}" if av else None


def _points(settings: dict, key="fpts") -> float:
    return settings.get(key, 0) + settings.get(f"{key}_decimal", 0) / 100


def managers(users: list[dict], rosters: list[dict]) -> list[dict]:
    """One card per claimed team: team name, owner, co-owners, avatar, record."""
    by_id = {u["user_id"]: u for u in users}
    out = []
    for r in rosters:
        if not r.get("owner_id"):
            continue
        owner = by_id.get(r["owner_id"])
        s = r.get("settings", {})
        out.append({
            "team": team_name(owner),
            "owner": (owner or {}).get("display_name", "—"),
            "avatar": avatar_url(owner),
            "co_owners": [by_id[c]["display_name"] for c in (r.get("co_owners") or []) if c in by_id],
            "wins": s.get("wins", 0),
            "losses": s.get("losses", 0),
            "ties": s.get("ties", 0),
            "pf": round(_points(s), 1),
        })
    out.sort(key=lambda m: m["team"].lower())
    return out


def standings(users: list[dict], rosters: list[dict]) -> list[dict]:
    by_id = {u["user_id"]: u for u in users}
    rows = []
    for r in rosters:
        if not r.get("owner_id"):
            continue
        owner = by_id.get(r["owner_id"])
        s = r.get("settings", {})
        rows.append({
            "team": team_name(owner),
            "owner": (owner or {}).get("display_name", "—"),
            "avatar": avatar_url(owner),
            "wins": s.get("wins", 0),
            "losses": s.get("losses", 0),
            "ties": s.get("ties", 0),
            "pf": round(_points(s), 1),
            "pa": round(_points(s, "fpts_against"), 1),
        })
    rows.sort(key=lambda x: (x["wins"], x["pf"]), reverse=True)  # wins, then points-for
    for i, row in enumerate(rows, 1):
        row["rank"] = i
    return rows


def scoreboard(matchups: list[dict], rosters: list[dict], users: list[dict]) -> list[dict]:
    """Pair matchup entries by matchup_id into head-to-heads."""
    by_id = {u["user_id"]: u for u in users}
    owner_of = {r["roster_id"]: by_id.get(r.get("owner_id")) for r in rosters}

    def side(entry: dict) -> dict:
        u = owner_of.get(entry["roster_id"])
        return {"team": team_name(u), "avatar": avatar_url(u), "points": round(entry.get("points") or 0, 1)}

    groups: dict = {}
    for m in matchups:
        groups.setdefault(m.get("matchup_id"), []).append(m)

    games = []
    for mid, entries in sorted(groups.items(), key=lambda kv: (kv[0] is None, kv[0])):
        sides = [side(e) for e in entries]
        winner = None
        if len(sides) == 2 and sides[0]["points"] != sides[1]["points"]:
            winner = 0 if sides[0]["points"] > sides[1]["points"] else 1
        games.append({"sides": sides, "winner": winner})
    return games


def player_line(pid: str, players: dict) -> dict:
    p = players.get(pid) or {}
    pos = p.get("position") or ("DEF" if pid.isalpha() else "")
    name = p.get("full_name") or f"{p.get('first_name', '')} {p.get('last_name', '')}".strip()
    if not name:
        name = f"{pid} D/ST" if pid.isalpha() else pid
    return {"name": name, "pos": pos, "team": p.get("team") or ""}


def team_rosters(rosters: list[dict], users: list[dict], players: dict) -> list[dict]:
    by_id = {u["user_id"]: u for u in users}
    out = []
    for r in rosters:
        if not r.get("owner_id"):
            continue
        owner = by_id.get(r["owner_id"])
        starters = [pid for pid in (r.get("starters") or []) if pid and pid != "0"]
        all_players = [pid for pid in (r.get("players") or []) if pid and pid != "0"]
        bench = [pid for pid in all_players if pid not in set(starters)]
        out.append({
            "team": team_name(owner),
            "avatar": avatar_url(owner),
            "starters": [player_line(p, players) for p in starters],
            "bench": [player_line(p, players) for p in bench],
        })
    out.sort(key=lambda t: t["team"].lower())
    return out


def transactions(txns: list[dict], rosters: list[dict], users: list[dict], players: dict) -> list[dict]:
    by_id = {u["user_id"]: u for u in users}
    team_of = {r["roster_id"]: team_name(by_id.get(r.get("owner_id"))) for r in rosters}
    out = []
    for t in txns:
        if t.get("status") != "complete":
            continue
        rids = t.get("roster_ids") or []
        adds = [(player_line(pid, players)["name"], team_of.get(rid, "?"))
                for pid, rid in (t.get("adds") or {}).items()]
        drops = [(player_line(pid, players)["name"], team_of.get(rid, "?"))
                 for pid, rid in (t.get("drops") or {}).items()]
        out.append({
            "type": t.get("type", "move"),
            "teams": [team_of.get(rid, "?") for rid in rids],
            "adds": adds,
            "drops": drops,
            "created": t.get("created") or 0,
        })
    out.sort(key=lambda x: x["created"], reverse=True)
    return out
