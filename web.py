"""Poverty Franchises — read-only league portal (nfl.djiang.xyz).

Thin FastAPI layer: each route gathers Sleeper data (cached) → shapes it via views.py
→ renders the designed Jinja templates. No writes, no auth.
"""

import datetime as dt
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import views
from config import cfg
from sleeper import (
    get_league,
    get_matchups,
    get_nfl_state,
    get_player_stats,
    get_players,
    get_rosters,
    get_transactions,
    get_users,
)

app = FastAPI(title="Poverty Franchises")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")
TZ = ZoneInfo(cfg.timezone)
LID = cfg.league_id

NAV = [("home", "/", "League"), ("draftboard", "/draftboard", "Draft Board"),
       ("standings", "/standings", "Standings"), ("scoreboard", "/scoreboard", "Scoreboard"),
       ("rosters", "/rosters", "Rosters"), ("transactions", "/transactions", "Transactions")]
STATUS_LABEL = {"pre_draft": "Pre-Draft Season", "drafting": "Draft Underway",
                "in_season": "Regular Season", "complete": "Season Complete"}


def _fmt(pts) -> str:
    return f"{(pts or 0):.1f}"


async def _base_ctx(request: Request, active: str) -> dict:
    league = await get_league(LID)
    rosters = await get_rosters(LID)
    status, season = league["status"], league["season"]
    is_pre = status in ("pre_draft", "drafting")
    teams_in = sum(1 for r in rosters if r.get("owner_id"))
    total = league["total_rosters"]
    d = cfg.draft_date
    draft_line = f"{d:%b %-d}, {cfg.draft_time_label}" if d else "To be announced"
    return {
        "request": request,
        "nav_items": [{"href": h, "label": lbl, "current": k == active} for k, h, lbl in NAV],
        "season_tag": f"{season} · {STATUS_LABEL.get(status, status)}",
        "footer_note": draft_line if is_pre else "Records live via Sleeper",
        "is_pre": is_pre,
        "has_season": not is_pre,
        "draft_line": draft_line,
        "draft_date_label": f"{d:%b %-d}" if d else "the draft",
        "seated_line": f"{teams_in} of {total}",
        "_teams_in": teams_in,
        "_total": total,
    }


@app.get("/health")
async def health():
    return {"ok": True}


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    ctx = await _base_ctx(request, "home")
    users, rosters = await get_users(LID), await get_rosters(LID)
    teams_in, total = ctx["_teams_in"], ctx["_total"]

    ctx["teams"] = [{
        "initials": views.initials(m["team"]),
        "avatar": m["avatar"],
        "name": m["team"],
        "owner": "@" + m["owner"] + (" + " + ", ".join(m["co_owners"]) if m["co_owners"] else ""),
        "record": views.record_str(m["wins"], m["losses"], m["ties"]),
        "pf_label": "—" if ctx["is_pre"] else _fmt(m["pf"]),
    } for m in views.managers(users, rosters)]
    ctx["managers_note"] = f"{teams_in} of {total} seated"
    ctx["links"] = [
        {"label": "Join the League", "href": cfg.join_url, "external": True},
        {"label": "Open in Sleeper", "href": f"https://sleeper.com/leagues/{LID}", "external": True},
        {"label": "FF Wrapped", "href": "https://ffwrapped.com", "external": True},
        {"label": "League Rewind", "href": "https://leaguerewind.com", "external": True},
    ]

    if ctx["is_pre"]:
        open_seats = total - teams_in
        d = cfg.draft_date
        days = (d - dt.datetime.now(TZ).date()).days if d else None
        ctx["stat1"] = {"label": "Franchises Seated", "value": f"{teams_in} of {total}",
                        "note": f"{open_seats} seat{'s' if open_seats != 1 else ''} open" if open_seats else "Full house"}
        ctx["stat2"] = {"label": "Draft Day", "value": f"{d:%b %-d}" if d else "TBA",
                        "note": f"{cfg.draft_time_label}" + (f" · {days}d out" if days and days > 0 else "")}
    else:
        state = await get_nfl_state()
        ctx["stat1"] = {"label": "Week", "value": str(state.get("week") or 1), "note": STATUS_LABEL.get("in_season")}
        ctx["stat2"] = {"label": "Franchises", "value": str(total), "note": "Full house"}
    return templates.TemplateResponse(request, "home.html", ctx)


@app.get("/draftboard", response_class=HTMLResponse)
async def draftboard(request: Request, pos: str | None = None):
    ctx = await _base_ctx(request, "draftboard")
    players = await get_players()
    season = "2025"
    stats = await get_player_stats(season)
    if not stats:  # fall back if the latest season isn't published yet
        season = "2024"
        stats = await get_player_stats(season)
    pos = pos if pos in views.FANTASY_POS else None
    ctx["players"] = views.draft_board(players, stats, pos, limit=200)
    ctx["season_stat"] = season
    ctx["board_note"] = f"Top 200 by Sleeper rank · {season} PPR"
    ctx["pos_chips"] = [{"label": "All", "href": "/draftboard", "current": pos is None}] + [
        {"label": p, "href": f"/draftboard?pos={p}", "current": pos == p}
        for p in ("QB", "RB", "WR", "TE", "K", "DEF")]
    return templates.TemplateResponse(request, "draftboard.html", ctx)


@app.get("/standings", response_class=HTMLResponse)
async def standings(request: Request):
    ctx = await _base_ctx(request, "standings")
    users, rosters = await get_users(LID), await get_rosters(LID)
    ctx["teams"] = [{
        "rank": r["rank"], "name": r["team"], "owner": "@" + r["owner"], "avatar": r["avatar"],
        "record": views.record_str(r["wins"], r["losses"], r["ties"]),
        "pct": views.win_pct(r["wins"], r["losses"], r["ties"]),
        "pf": _fmt(r["pf"]), "pa": _fmt(r["pa"]),
    } for r in views.standings(users, rosters)]
    if ctx["is_pre"]:
        ctx["through_label"] = "Awaiting kickoff"
    else:
        state = await get_nfl_state()
        ctx["through_label"] = f"Through Week {state.get('week') or 1}"
    return templates.TemplateResponse(request, "standings.html", ctx)


@app.get("/scoreboard", response_class=HTMLResponse)
async def scoreboard(request: Request, week: int | None = None):
    ctx = await _base_ctx(request, "scoreboard")
    state = await get_nfl_state()
    current = state.get("week") or 1
    week = week or current
    users, rosters = await get_users(LID), await get_rosters(LID)
    games = views.scoreboard(await get_matchups(LID, week), rosters, users)

    matchups = []
    for i, g in enumerate(games):
        sides = g["sides"]
        a = sides[0]
        b = sides[1] if len(sides) > 1 else None
        entry_a = {"win": "true" if g["winner"] == 0 else "false", "avatar": a["avatar"],
                   "initials": views.initials(a["team"]), "name": a["team"], "score": _fmt(a["points"])}
        entry_b = ({"win": "true" if g["winner"] == 1 else "false", "avatar": b["avatar"],
                    "initials": views.initials(b["team"]), "name": b["team"], "score": _fmt(b["points"])}
                   if b else {"win": "false", "avatar": None, "initials": "—", "name": "Bye", "score": "—"})
        matchups.append({"slot": f"Match {i + 1}", "status": "" if ctx["is_pre"] else "Final",
                         "a": entry_a, "b": entry_b})

    ctx["matchups"] = matchups
    ctx["week_has_games"] = bool(matchups)
    ctx["week_empty"] = not matchups
    ctx["week_heading"] = f"Week {week}"
    ctx["weeks"] = [{"href": f"/scoreboard?week={w}", "label": str(w), "current": w == week} for w in range(1, 19)]
    ctx["empty_week_title"] = f"No matchups for Week {week}"
    ctx["empty_week_body"] = ("Scores appear here once the season starts."
                              + (f" Draft is {ctx['draft_date_label']}." if ctx["is_pre"] else ""))
    return templates.TemplateResponse(request, "scoreboard.html", ctx)


@app.get("/rosters", response_class=HTMLResponse)
async def rosters(request: Request, team: str | None = None):
    ctx = await _base_ctx(request, "rosters")
    ctx["roster_note"] = "Unfurnished"
    if ctx["has_season"]:
        users, rosters, league = await get_users(LID), await get_rosters(LID), await get_league(LID)
        players = await get_players()
        positions = league.get("roster_positions", [])
        by_id = {u["user_id"]: u for u in users}
        claimed = [r for r in rosters if r.get("owner_id")]
        valid = [str(r["roster_id"]) for r in claimed]
        active_id = team if team in valid else (valid[0] if valid else None)

        chips, active_team, active_roster = [], {"name": "", "owner": ""}, {"starters": [], "bench": []}
        for r in claimed:
            owner = by_id.get(r["owner_id"])
            name, rid = views.team_name(owner), str(r["roster_id"])
            chips.append({"href": f"/rosters?team={rid}", "label": name, "current": rid == active_id})
            if rid == active_id:
                active_team = {"name": name, "owner": "@" + (owner or {}).get("display_name", "")}
                active_roster = views.lineup(r, players, positions)
        ctx.update(team_chips=chips, active_team=active_team, active_roster=active_roster,
                   roster_note=active_team["name"])
    return templates.TemplateResponse(request, "rosters.html", ctx)


@app.get("/transactions", response_class=HTMLResponse)
async def transactions(request: Request):
    ctx = await _base_ctx(request, "transactions")
    ctx["tx_note"] = "No entries"
    if ctx["has_season"]:
        state = await get_nfl_state()
        current = state.get("week") or 1
        users, rosters = await get_users(LID), await get_rosters(LID)
        raw = []
        for wk in range(1, current + 1):
            raw += await get_transactions(LID, wk)
        players = await get_players() if raw else {}
        kinds = {"trade": "Trade", "waiver": "Waiver", "free_agent": "Add"}
        feed = []
        for e in views.transactions(raw, rosters, users, players):
            moves = [f"added {n}" for n, _ in e["adds"]] + [f"dropped {n}" for n, _ in e["drops"]]
            date = (dt.datetime.fromtimestamp(e["created"] / 1000, TZ).strftime("%b %-d")
                    if e["created"] else "")
            feed.append({"date": date, "kind": kinds.get(e["type"], e["type"].title()),
                         "team": e["teams"][0] if e["teams"] else "—", "text": ", ".join(moves) or "—"})
        ctx["transactions"] = feed
        ctx["tx_note"] = f"{len(feed)} move{'s' if len(feed) != 1 else ''}"
    return templates.TemplateResponse(request, "transactions.html", ctx)
