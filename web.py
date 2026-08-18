"""Poverty Franchises — read-only league portal (nfl.djiang.xyz).

Thin FastAPI layer: each route gathers Sleeper data (cached) → shapes it via views.py
→ renders the designed Jinja templates. No writes, no auth.
"""

import datetime as dt
import secrets
import tomllib
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

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
    resolve_user_id,
)

app = FastAPI(title="Poverty Franchises")
app.add_middleware(SessionMiddleware, secret_key=cfg.session_secret, https_only=True, same_site="lax")
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


def _discord_map() -> dict:
    """discord_id -> Sleeper handle, straight from the bot's expected.toml (shared clone)."""
    try:
        with open("expected.toml", "rb") as f:
            data = tomllib.load(f)
    except FileNotFoundError:
        return {}
    return {m["discord_id"]: m["sleeper"] for m in data.get("member", []) if m.get("discord_id")}


async def _me_roster_id(request: Request):
    did = request.session.get("discord_id")
    if not did:
        return None
    handle = _discord_map().get(int(did))
    if not handle:
        return None
    uid = await resolve_user_id(handle)
    if not uid:
        return None
    for r in await get_rosters(LID):
        if r.get("owner_id") == uid or uid in (r.get("co_owners") or []):
            return r["roster_id"]
    return None


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
    me = await _me_roster_id(request)
    return {
        "request": request,
        "oauth_enabled": cfg.oauth_enabled,
        "logged_in": bool(request.session.get("discord_id")),
        "me_roster_id": me,
        "my_team_href": f"/team/{me}" if me else None,
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
        "rank": r["rank"], "name": r["team"], "avatar": r["avatar"], "roster_id": r["roster_id"],
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


@app.get("/login")
async def login(request: Request):
    if not cfg.oauth_enabled:
        return RedirectResponse("/")
    state = secrets.token_urlsafe(16)
    request.session["oauth_state"] = state
    q = urlencode({"client_id": cfg.discord_client_id, "redirect_uri": cfg.oauth_redirect,
                   "response_type": "code", "scope": "identify", "state": state})
    return RedirectResponse(f"https://discord.com/oauth2/authorize?{q}")


@app.get("/auth/callback")
async def auth_callback(request: Request, code: str | None = None, state: str | None = None):
    if not cfg.oauth_enabled or not code or state != request.session.get("oauth_state"):
        return RedirectResponse("/")
    async with httpx.AsyncClient(timeout=15) as c:
        tok = await c.post("https://discord.com/api/oauth2/token", data={
            "client_id": cfg.discord_client_id, "client_secret": cfg.discord_client_secret,
            "grant_type": "authorization_code", "code": code, "redirect_uri": cfg.oauth_redirect})
        tok.raise_for_status()
        access = tok.json()["access_token"]
        me = (await c.get("https://discord.com/api/users/@me",
                          headers={"Authorization": f"Bearer {access}"})).json()
    request.session["discord_id"] = me["id"]
    request.session.pop("oauth_state", None)
    return RedirectResponse("/")


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/")


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
    ctx["headline"] = views.player_headline(st)
    ctx["stat_lines"] = views.player_stat_lines(position, st)
    ctx["pid"] = pid
    return templates.TemplateResponse(request, "player.html", ctx)


def _profile(pid: str, players: dict, st: dict, season: str) -> dict:
    p = players.get(pid, {})
    position = p.get("position") or ""
    return {
        "pid": pid,
        "name": p.get("full_name") or f"{p.get('first_name', '')} {p.get('last_name', '')}".strip() or pid,
        "pos": position, "team": p.get("team") or "FA",
        "img": views.player_image(pid, position, p.get("team") or pid),
        "age": p.get("age"), "height": views.height_str(p.get("height")), "weight": p.get("weight"),
        "college": p.get("college") or "—",
        "exp": "Rookie" if p.get("years_exp") == 0 else (f"{p.get('years_exp')} yrs" if p.get("years_exp") is not None else "—"),
        "headline": views.player_headline(st), "stat_lines": views.player_stat_lines(position, st),
    }


@app.get("/api/players")
async def api_players():
    players = await get_players()
    out = [{"id": pid, "name": p.get("full_name") or pid,
            "pos": p.get("position"), "team": p.get("team") or ""}
           for pid, p in players.items()
           if p.get("search_rank") and p["search_rank"] < 100000
           and p.get("position") in views.FANTASY_POS and (p.get("team") or p.get("position") == "DEF")]
    out.sort(key=lambda x: x["name"])
    return out


@app.get("/compare", response_class=HTMLResponse)
async def compare(request: Request, a: str, b: str | None = None):
    players = await get_players()
    if a not in players:
        return RedirectResponse("/draftboard")
    season = "2025"
    stats = await get_player_stats(season)
    if not stats:
        season = "2024"
        stats = await get_player_stats(season)
    ctx = await _base_ctx(request, "")
    ctx["season_stat"] = season
    ctx["a"] = _profile(a, players, stats.get(a, {}), season)
    ctx["b"] = _profile(b, players, stats.get(b, {}), season) if b in players else None
    return templates.TemplateResponse(request, "compare.html", ctx)


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
