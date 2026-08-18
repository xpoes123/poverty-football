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
        games.append({"sides": sides, "winner": winner, "mid": mid})
    return games


def matchup_detail(week_matchups: list[dict], mid: int, rosters: list[dict], users: list[dict],
                   players: dict, slots: list[str], week_stats: dict | None = None) -> dict | None:
    """Both sides of one matchup with a per-starter points breakdown + weekly box score."""
    week_stats = week_stats or {}
    by_id = {u["user_id"]: u for u in users}
    owner_of = {r["roster_id"]: by_id.get(r.get("owner_id")) for r in rosters}
    entries = [m for m in week_matchups if m.get("matchup_id") == mid]
    if len(entries) < 2:
        return None

    def side(m):
        starters = m.get("starters") or []
        sp = m.get("starters_points") or []
        pp = m.get("players_points") or {}
        rows = []
        for i, pid in enumerate(starters):
            slot = slots[i] if i < len(slots) else "FLEX"
            if not pid or pid == "0":
                rows.append({"slot": slot, "name": "—", "pos": "", "img": None, "pts": 0.0, "stats": []})
                continue
            pl = player_line(str(pid), players)
            pts = sp[i] if i < len(sp) else pp.get(str(pid), 0)
            rows.append({"slot": slot, "name": pl["name"], "pos": pl["pos"],
                         "img": player_image(str(pid), pl["pos"], pl["team"]), "pts": round(pts or 0, 1),
                         "stats": player_stat_lines(pl["pos"], week_stats.get(str(pid), {}))})
        u = owner_of.get(m["roster_id"])
        return {"team": team_name(u), "avatar": avatar_url(u),
                "points": round(m.get("points") or 0, 1), "starters": rows}

    a, b = side(entries[0]), side(entries[1])
    winner = None if a["points"] == b["points"] else (0 if a["points"] > b["points"] else 1)
    return {"a": a, "b": b, "winner": winner}


def luck_table(weeks: list[list[dict]], users: list[dict], rosters: list[dict]) -> list[dict]:
    """Luck / expected-wins per franchise over the PLAYED `weeks` (raw Sleeper matchup arrays).

    All-play: each week a roster is credited a win vs every other roster it outscored and a
    loss vs every roster that outscored it (equal points count for neither). Expected wins =
    all-play win pct * games; luck = actual head-to-head wins - expected. Pure, no I/O."""
    by_id = {u["user_id"]: u for u in users}
    owner_of = {r["roster_id"]: by_id.get(r.get("owner_id")) for r in rosters if r.get("owner_id")}
    ids = set(owner_of)
    if not weeks or not ids:
        return []
    acc = {rid: {"games": 0, "w": 0, "l": 0, "t": 0, "apw": 0, "apl": 0, "pts": 0.0} for rid in ids}
    for wk in weeks:
        entries = [m for m in wk if m.get("roster_id") in ids]
        pts = {m["roster_id"]: (m.get("points") or 0) for m in entries}
        # head-to-head, paired by matchup_id like scoreboard()
        groups: dict = {}
        for m in entries:
            groups.setdefault(m.get("matchup_id"), []).append(m)
        for grp in groups.values():
            if len(grp) != 2:
                continue
            a, b = grp[0], grp[1]
            ra, rb = a["roster_id"], b["roster_id"]
            pa, pb = pts[ra], pts[rb]
            if pa == pb:
                acc[ra]["t"] += 1; acc[rb]["t"] += 1
            elif pa > pb:
                acc[ra]["w"] += 1; acc[rb]["l"] += 1
            else:
                acc[rb]["w"] += 1; acc[ra]["l"] += 1
        # all-play + games + points
        for rid, p in pts.items():
            acc[rid]["games"] += 1
            acc[rid]["pts"] += p
            for orid, op in pts.items():
                if orid == rid:
                    continue
                if p > op:
                    acc[rid]["apw"] += 1
                elif p < op:
                    acc[rid]["apl"] += 1
    rows = []
    for rid, a in acc.items():
        if a["games"] == 0:
            continue
        ap = a["apw"] + a["apl"]
        exp = round(a["apw"] / ap * a["games"], 1) if ap else 0
        rows.append({
            "roster_id": rid,
            "team": team_name(owner_of.get(rid)),
            "avatar": avatar_url(owner_of.get(rid)),
            "wins": a["w"], "losses": a["l"], "ties": a["t"],
            "all_play": record_str(a["apw"], a["apl"], 0),
            "expected_wins": exp,
            "luck": round(a["w"] - exp, 1),
            "avg_pts": round(a["pts"] / a["games"], 1),
        })
    rows.sort(key=lambda r: r["luck"], reverse=True)
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return rows


def draft_results(picks: list[dict], users: list[dict], rosters: list[dict], players: dict) -> list[dict]:
    """Completed draft grouped by round: each pick's overall number, team, and player."""
    by_id = {u["user_id"]: u for u in users}
    owner_of = {r["roster_id"]: by_id.get(r.get("owner_id")) for r in rosters}
    rounds: dict = {}
    for p in sorted(picks, key=lambda x: x.get("pick_no") or 0):
        u = owner_of.get(p.get("roster_id"))
        pl = player_line(str(p.get("player_id")), players)
        rounds.setdefault(p.get("round") or 0, []).append({
            "overall": p.get("pick_no"),
            "team": team_name(u), "avatar": avatar_url(u),
            "player": pl["name"], "pos": pl["pos"], "img": player_image(str(p.get("player_id")), pl["pos"], pl["team"]),
        })
    return [{"round": r, "picks": rounds[r]} for r in sorted(rounds) if r]


def game_players(away: str, home: str, week_stats: dict, players: dict,
                 rosters: list[dict], users: list[dict], scoring: dict) -> dict:
    """Fantasy-relevant players in one NFL game, scored by the league's own settings, with
    the franchise that rosters each. Grouped by NFL team, sorted by fantasy points."""
    by_uid = {u["user_id"]: u for u in users}
    owner = {}
    for r in rosters:
        if r.get("owner_id"):
            fr = team_name(by_uid.get(r["owner_id"]))
            for pid in (r.get("players") or []):
                owner[str(pid)] = fr

    def fpts(st):
        return round(sum((v or 0) * scoring.get(k, 0) for k, v in st.items()
                         if isinstance(v, (int, float)) and k in scoring), 2)

    groups = {"away": [], "home": []}
    for pid, p in players.items():
        team = p.get("team")
        if team not in (away, home) or p.get("position") not in FANTASY_POS:
            continue
        st = week_stats.get(pid)
        if not st:
            continue
        pl = player_line(pid, players)
        groups["away" if team == away else "home"].append({
            "pid": pid, "name": pl["name"], "pos": pl["pos"], "pts": fpts(st),
            "franchise": owner.get(pid), "img": player_image(pid, pl["pos"], team),
            "stats": player_stat_lines(pl["pos"], st),
        })
    for k in groups:
        groups[k].sort(key=lambda r: r["pts"], reverse=True)
    return groups


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
        st = stats.get(pid) or {}
        pts = st.get("pts_ppr")
        gp = st.get("gp")
        ppg = round(pts / gp, 1) if (pts and gp) else None
        name = p.get("full_name") or f"{p.get('first_name', '')} {p.get('last_name', '')}".strip() or pid
        rows.append({
            "pid": pid, "sr": sr, "name": name, "pos": position, "team": p.get("team") or "FA",
            "img": player_image(pid, position, p.get("team") or pid),
            "age": str(age) if age is not None else "—", "age_n": age if age is not None else -1,
            "exp": "R" if exp == 0 else (str(exp) if exp is not None else "—"),
            "exp_n": exp if exp is not None else -1,
            "gp": str(int(gp)) if gp else "—", "gp_n": int(gp) if gp else -1,
            "pts": str(round(pts)) if pts else "—", "pts_n": round(pts) if pts else -1,
            "ppg": str(ppg) if ppg else "—", "ppg_n": ppg if ppg else -1,
        })
    rows.sort(key=lambda r: r["sr"])
    if limit:
        rows = rows[:limit]
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return rows


STAT_SPECS = {
    "QB": [("Games", "gp"), ("Pass yds", "pass_yd"), ("Pass TD", "pass_td"), ("INT", "pass_int"),
           ("Rush yds", "rush_yd"), ("Rush TD", "rush_td")],
    "RB": [("Games", "gp"), ("Rush yds", "rush_yd"), ("Rush TD", "rush_td"), ("Targets", "rec_tgt"),
           ("Rec", "rec"), ("Rec yds", "rec_yd"), ("Rec TD", "rec_td")],
    "WR": [("Games", "gp"), ("Targets", "rec_tgt"), ("Rec", "rec"), ("Rec yds", "rec_yd"),
           ("Rec TD", "rec_td"), ("Rush yds", "rush_yd")],
    "TE": [("Games", "gp"), ("Targets", "rec_tgt"), ("Rec", "rec"), ("Rec yds", "rec_yd"), ("Rec TD", "rec_td")],
    "K": [("Games", "gp"), ("FG made", "fgm"), ("FG att", "fga"), ("XP made", "xpm")],
    "DEF": [("Games", "gp"), ("Sacks", "sack"), ("INT", "int"), ("Fum rec", "fum_rec"), ("Def TD", "def_td")],
}


def _num(v):
    if v is None:
        return None
    return int(v) if float(v).is_integer() else round(v, 1)


def player_headline(st: dict) -> list[dict]:
    """The three big numbers shown in the profile stat strip."""
    pts, gp = st.get("pts_ppr"), st.get("gp")
    ppg = round(pts / gp, 1) if (pts and gp) else None
    trio = [("PPR", _num(pts)), ("PPR / GM", ppg), ("Games", _num(gp))]
    return [{"k": k, "v": v if v is not None else "—"} for k, v in trio]


def player_stat_lines(position: str, st: dict) -> list[dict]:
    """Position-relevant season stat lines (label + value); games/PPR live in the headline."""
    lines = []
    for label, key in STAT_SPECS.get(position, []):
        if key == "gp":
            continue
        val = _num(st.get(key))
        if val is not None:
            lines.append({"label": label, "value": val})
    return lines


def height_str(inches) -> str:
    try:
        n = int(inches)
        return f"{n // 12}'{n % 12}\""
    except (TypeError, ValueError):
        return "—"


def lineup(roster: dict, players: dict, roster_positions: list[str]) -> dict:
    """Starters mapped to their lineup slots (QB/RB/FLEX/…), then the bench."""
    slots = [p for p in roster_positions if p != "BN"]
    starter_ids = [pid for pid in (roster.get("starters") or []) if pid and pid != "0"]
    starter_set = set(starter_ids)
    bench_ids = [pid for pid in (roster.get("players") or []) if pid and pid != "0" and pid not in starter_set]

    def entry(slot: str, pid: str) -> dict:
        p = player_line(pid, players)
        return {"slot": slot, "n": p["name"], "meta": p["team"] or "FA",
                "img": player_image(pid, p["pos"], p["team"])}

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
