"""Poverty Franchises — read-only league portal (nfl.djiang.xyz).

Thin FastAPI layer: each route gathers Sleeper data (cached) → shapes it via views.py
→ renders a Jinja template. No writes, no auth. Templates are placeholders until the
designed HTML drops in; the context each route passes won't change.
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


def _draft_ctx() -> dict:
    d = cfg.draft_date
    days = None if d is None else (d - dt.datetime.now(TZ).date()).days
    return {"draft_date": d, "draft_time": cfg.draft_time_label, "draft_days": days}


async def _base_ctx(request: Request, active: str) -> dict:
    league = await get_league(LID)
    return {
        "request": request,
        "active": active,
        "league_name": league["name"],
        "season": league["season"],
        "status": league["status"],  # pre_draft | drafting | in_season | complete
        "pre_draft": league["status"] in ("pre_draft", "drafting"),
        "join_url": cfg.join_url,
        **_draft_ctx(),
    }


@app.get("/health")
async def health():
    return {"ok": True}


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    users, rosters, league = await get_users(LID), await get_rosters(LID), await get_league(LID)
    ctx = await _base_ctx(request, "home")
    ctx["managers"] = views.managers(users, rosters)
    ctx["teams_in"] = sum(1 for r in rosters if r.get("owner_id"))
    ctx["total_teams"] = league["total_rosters"]
    return templates.TemplateResponse(request, "home.html", ctx)


@app.get("/standings", response_class=HTMLResponse)
async def standings(request: Request):
    users, rosters = await get_users(LID), await get_rosters(LID)
    ctx = await _base_ctx(request, "standings")
    ctx["rows"] = views.standings(users, rosters)
    return templates.TemplateResponse(request, "standings.html", ctx)


@app.get("/scoreboard", response_class=HTMLResponse)
async def scoreboard(request: Request, week: int | None = None):
    state = await get_nfl_state()
    current = state.get("week") or 1
    week = week or current
    users, rosters = await get_users(LID), await get_rosters(LID)
    matchups = await get_matchups(LID, week)
    ctx = await _base_ctx(request, "scoreboard")
    ctx["games"] = views.scoreboard(matchups, rosters, users)
    ctx["week"] = week
    ctx["weeks"] = list(range(1, 19))
    ctx["current_week"] = current
    return templates.TemplateResponse(request, "scoreboard.html", ctx)


@app.get("/rosters", response_class=HTMLResponse)
async def rosters(request: Request):
    users, rosters = await get_users(LID), await get_rosters(LID)
    ctx = await _base_ctx(request, "rosters")
    players = await get_players() if any(r.get("players") for r in rosters) else {}
    ctx["teams"] = views.team_rosters(rosters, users, players)
    return templates.TemplateResponse(request, "rosters.html", ctx)


@app.get("/transactions", response_class=HTMLResponse)
async def transactions(request: Request):
    state = await get_nfl_state()
    current = state.get("week") or 1
    users, rosters = await get_users(LID), await get_rosters(LID)
    raw = []
    for wk in range(1, current + 1):  # merge the season's completed weeks into one feed
        raw += await get_transactions(LID, wk)
    ctx = await _base_ctx(request, "transactions")
    players = await get_players() if raw else {}
    ctx["feed"] = views.transactions(raw, rosters, users, players)
    return templates.TemplateResponse(request, "transactions.html", ctx)
