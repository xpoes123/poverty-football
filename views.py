"""Pure shaping of raw Sleeper payloads into view models. No network — unit-testable."""

CDN = "https://sleepercdn.com/avatars/thumbs"


def initials(name: str) -> str:
    parts = [p for p in name.split() if p]
    if len(parts) >= 2:
        return (parts[0][0] + parts[1][0]).upper()
    return (name[:2] or "—").upper()


def record_str(w: int, l: int, t: int) -> str:
    return f"{w}–{l}" + (f"–{t}" if t else "")


def win_pct(w: int, l: int, t: int) -> str:
    g = w + l + t
    if not g:
        return "—"
    s = f"{(w + 0.5 * t) / g:.3f}"
    return s[1:] if s.startswith("0") else s  # ".667", but keep "1.000"


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
            "roster_id": r["roster_id"],
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
            "roster_id": r["roster_id"],
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


FANTASY_POS = {"QB", "RB", "WR", "TE", "K", "DEF"}


def player_image(pid: str, position: str, team: str | None) -> str | None:
    """Sleeper CDN: headshot by player id, or the team logo for a defense."""
    if position == "DEF":
        t = (team or pid or "").lower()
        return f"https://sleepercdn.com/images/team_logos/nfl/{t}.png" if t else None
    return f"https://sleepercdn.com/content/nfl/players/thumb/{pid}.jpg"


def draft_board(players: dict, stats: dict, pos: str | None = None,
                limit: int | None = None, exclude: set | None = None) -> list[dict]:
    """Every fantasy-relevant player ordered by Sleeper's search_rank (1 = most valued),
    with headshot, age, experience and last-season PPR. Each row carries both a display
    string and an `*_n` numeric key so the table can be sorted client-side. `exclude` drops
    already-rostered player ids (used for the free-agent pool)."""
    rows = []
    for pid, p in players.items():
        sr = p.get("search_rank")
        position = p.get("position")
        if not sr or sr >= 100000 or position not in FANTASY_POS:
            continue
        if position != "DEF" and not p.get("team"):  # drop free agents / inactive
            continue
        if pos and position != pos:
            continue
        if exclude and pid in exclude:
            continue
        exp = p.get("years_exp")
        age = p.get("age")
        pts = (stats.get(pid) or {}).get("pts_ppr")
        name = p.get("full_name") or f"{p.get('first_name', '')} {p.get('last_name', '')}".strip() or pid
        rows.append({
            "sr": sr, "name": name, "pos": position, "team": p.get("team") or "FA",
            "img": player_image(pid, position, p.get("team") or pid),
            "age": str(age) if age is not None else "—", "age_n": age if age is not None else -1,
            "exp": "R" if exp == 0 else (str(exp) if exp is not None else "—"),
            "exp_n": exp if exp is not None else -1,
            "pts": str(round(pts)) if pts else "—", "pts_n": round(pts) if pts else -1,
        })
    rows.sort(key=lambda r: r["sr"])
    if limit:
        rows = rows[:limit]
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return rows


def lineup(roster: dict, players: dict, roster_positions: list[str]) -> dict:
    """Starters mapped to their lineup slots (QB/RB/FLEX/…), then the bench."""
    slots = [p for p in roster_positions if p != "BN"]
    starter_ids = [pid for pid in (roster.get("starters") or []) if pid and pid != "0"]
    starter_set = set(starter_ids)
    bench_ids = [pid for pid in (roster.get("players") or []) if pid and pid != "0" and pid not in starter_set]

    def entry(slot: str, pid: str) -> dict:
        p = player_line(pid, players)
        return {"slot": slot, "n": p["name"], "meta": p["team"] or "FA"}

    starters = [entry(slots[i] if i < len(slots) else "FLEX", pid) for i, pid in enumerate(starter_ids)]
    bench = [entry(player_line(pid, players)["pos"] or "BN", pid) for pid in bench_ids]
    return {"starters": starters, "bench": bench}


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
