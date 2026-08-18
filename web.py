"""Poverty Franchises — read-only league portal (nfl.djiang.xyz).

Thin FastAPI layer: each route gathers Sleeper data (cached) → shapes it via views.py
→ renders the designed Jinja templates. No writes, no auth.
"""

import datetime as dt
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
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
DRAFT_HOUR = 20  # 8 PM ET, matches cfg.draft_time_label

NAV = [("home", "/", "League"), ("draftboard", "/draftboard", "Draft Board"),
       ("freeagents", "/freeagents", "Free Agents"), ("schedule", "/schedule", "Schedule")]
TX_KINDS = {"trade": "Trade", "waiver": "Waiver", "free_agent": "Add"}
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
    target = dt.datetime.combine(d, dt.time(DRAFT_HOUR), tzinfo=TZ) if d else None
    upcoming = target and target > dt.datetime.now(TZ)
    return {
        "request": request,
        "nav_items": [{"href": h, "label": lbl, "current": k == active} for k, h, lbl in NAV],
        "season_tag": f"{season} · {STATUS_LABEL.get(status, status)}",
        "footer_note": draft_line if is_pre else "Records live via Sleeper",
        "is_pre": is_pre,
        "has_season": not is_pre,
        "draft_line": draft_line,
        "draft_date_label": f"{d:%b %-d}" if d else "the draft",
        "draft_target": target.isoformat() if upcoming else None,
        "draft_when": f"{d:%b %-d, %Y} · {cfg.draft_time_label}" if d else "",
        "seated_line": f"{teams_in} of {total}",
        "_teams_in": teams_in,
        "_total": total,
    }


def _standings_rows(users, rosters):
    return [{
        "rank": r["rank"], "name": r["team"], "owner": "@" + r["owner"], "avatar": r["avatar"],
        "href": f"/team/{r['roster_id']}",
        "record": views.record_str(r["wins"], r["losses"], r["ties"]),
        "pct": views.win_pct(r["wins"], r["losses"], r["ties"]),
        "pf": _fmt(r["pf"]), "pa": _fmt(r["pa"]),
    } for r in views.standings(users, rosters)]


async def _tx_feed(users, rosters):
    state = await get_nfl_state()
    raw = []
    for wk in range(1, (state.get("week") or 1) + 1):
        raw += await get_transactions(LID, wk)
    players = await get_players() if raw else {}
    feed = []
    for e in views.transactions(raw, rosters, users, players):
        moves = [f"added {n}" for n, _ in e["adds"]] + [f"dropped {n}" for n, _ in e["drops"]]
        date = (dt.datetime.fromtimestamp(e["created"] / 1000, TZ).strftime("%b %-d") if e["created"] else "")
        feed.append({"date": date, "kind": TX_KINDS.get(e["type"], e["type"].title()),
                     "team": e["teams"][0] if e["teams"] else "—", "text": ", ".join(moves) or "—"})
    return feed


@app.get("/health")
async def health():
    return {"ok": True}


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    ctx = await _base_ctx(request, "home")
    users, rosters = await get_users(LID), await get_rosters(LID)

    ctx["links"] = [
        {"label": "Join the League", "href": cfg.join_url, "external": True},
        {"label": "Open in Sleeper", "href": f"https://sleeper.com/leagues/{LID}", "external": True},
    ]
    ctx["rows"] = _standings_rows(users, rosters)
    ctx["tx_feed"] = await _tx_feed(users, rosters) if ctx["has_season"] else []
    if ctx["is_pre"]:
        ctx["table_title"] = "Franchises"
        ctx["through_label"] = f"{ctx['seated_line']} seated"
    else:
        state = await get_nfl_state()
        wk = state.get("week") or 1
        ctx["table_title"] = "Standings"
        ctx["through_label"] = f"Through Week {wk}"
    return templates.TemplateResponse(request, "home.html", ctx)


async def _board(request: Request, active: str, heading: str, base_href: str,
                 pos: str | None, exclude: set | None):
    ctx = await _base_ctx(request, active)
    players = await get_players()
    season = "2025"
    stats = await get_player_stats(season)
    if not stats:
        season = "2024"
        stats = await get_player_stats(season)
    pos = pos if pos in views.FANTASY_POS else None
    rows = views.draft_board(players, stats, pos, exclude=exclude)
    ctx["players"] = rows
    ctx["season_stat"] = season
    ctx["board_heading"] = heading
    ctx["board_note"] = f"{len(rows)} players · {season} PPR · tap a header to sort"
    ctx["pos_chips"] = [{"label": "All", "href": base_href, "current": pos is None}] + [
        {"label": p, "href": f"{base_href}?pos={p}", "current": pos == p}
        for p in ("QB", "RB", "WR", "TE", "K", "DEF")]
    return templates.TemplateResponse(request, "board.html", ctx)


@app.get("/draftboard", response_class=HTMLResponse)
async def draftboard(request: Request, pos: str | None = None):
    return await _board(request, "draftboard", "Draft Board", "/draftboard", pos, exclude=None)


@app.get("/freeagents", response_class=HTMLResponse)
async def freeagents(request: Request, pos: str | None = None):
    rosters = await get_rosters(LID)
    rostered = {pid for r in rosters for pid in (r.get("players") or []) if pid}
    return await _board(request, "freeagents", "Free Agents", "/freeagents", pos, exclude=rostered)


@app.get("/team/{roster_id}", response_class=HTMLResponse)
async def team_page(request: Request, roster_id: int):
    users, rosters, league = await get_users(LID), await get_rosters(LID), await get_league(LID)
    roster = next((r for r in rosters if r["roster_id"] == roster_id and r.get("owner_id")), None)
    if roster is None:
        return RedirectResponse("/")
    ctx = await _base_ctx(request, "")
    by_id = {u["user_id"]: u for u in users}
    owner = by_id.get(roster["owner_id"])
    s = roster.get("settings", {})
    rank = next((row["rank"] for row in views.standings(users, rosters)
                 if row["roster_id"] == roster_id), None)
    ctx["team"] = {
        "name": views.team_name(owner),
        "owner": (owner or {}).get("display_name", "—"),
        "avatar": views.avatar_url(owner),
        "co_owners": [by_id[c]["display_name"] for c in (roster.get("co_owners") or []) if c in by_id],
        "record": views.record_str(s.get("wins", 0), s.get("losses", 0), s.get("ties", 0)),
        "pf": _fmt(s.get("fpts", 0) + s.get("fpts_decimal", 0) / 100),
        "pa": _fmt(s.get("fpts_against", 0) + s.get("fpts_against_decimal", 0) / 100),
        "rank": rank,
    }
    if ctx["has_season"]:
        players = await get_players()
        ctx["lineup"] = views.lineup(roster, players, league.get("roster_positions", []))
    return templates.TemplateResponse(request, "team.html", ctx)


@app.get("/player/{pid}", response_class=HTMLResponse)
async def player_profile(request: Request, pid: str):
    players = await get_players()
    p = players.get(pid)
    if p is None:
        return RedirectResponse("/draftboard")
    ctx = await _base_ctx(request, "")
    season = "2025"
    st = (await get_player_stats(season)).get(pid, {})
    if not st:
        season = "2024"
        st = (await get_player_stats(season)).get(pid, {})
    position = p.get("position") or ""
    espn_id = p.get("espn_id")
    ctx["player"] = {
        "name": p.get("full_name") or f"{p.get('first_name', '')} {p.get('last_name', '')}".strip() or pid,
        "pos": position, "team": p.get("team") or "FA",
        "img": views.player_image(pid, position, p.get("team") or pid),
        "number": p.get("number"), "age": p.get("age"),
        "height": views.height_str(p.get("height")), "weight": p.get("weight"),
        "college": p.get("college") or "—",
        "exp": "Rookie" if p.get("years_exp") == 0 else (f"{p.get('years_exp')} yrs" if p.get("years_exp") is not None else "—"),
        "status": p.get("injury_status") or p.get("status") or "Active",
        "espn_url": f"https://www.espn.com/nfl/player/_/id/{espn_id}" if espn_id else None,
    }
    ctx["season_stat"] = season
    ctx["stat_lines"] = views.player_stat_lines(position, st)
    return templates.TemplateResponse(request, "player.html", ctx)


@app.get("/schedule", response_class=HTMLResponse)
async def schedule(request: Request, week: int | None = None):
    ctx = await _base_ctx(request, "schedule")
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
    ctx["weeks"] = [{"href": f"/schedule?week={w}", "label": str(w), "current": w == week} for w in range(1, 19)]
    ctx["empty_week_title"] = f"No matchups for Week {week}"
    ctx["empty_week_body"] = ("Scores appear here once the season starts."
                              + (f" Draft is {ctx['draft_date_label']}." if ctx["is_pre"] else ""))
    return templates.TemplateResponse(request, "schedule.html", ctx)
