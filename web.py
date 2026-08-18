"""Poverty Franchises — read-only league portal (nfl.djiang.xyz).

Thin FastAPI layer: each route gathers Sleeper data (cached) → shapes it via views.py
→ renders the designed Jinja templates. No writes, no auth.
"""

import datetime as dt
import secrets
import tomllib
from urllib.parse import parse_qs, urlencode, urlparse
from zoneinfo import ZoneInfo

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

import betting
import h2h
import odds
import espn
import views
from config import cfg
from sleeper import (
    get_draft_picks,
    get_drafts,
    get_league,
    get_matchups,
    get_nfl_state,
    get_player_stats,
    get_players,
    get_week_stats,
    get_rosters,
    get_transactions,
    get_users,
    resolve_user_id,
    seed_preview,
)

app = FastAPI(title="Poverty Franchises")


class SeedPreviewMiddleware:
    """Lets a visitor preview seeded season data via a `seed_preview` cookie — per-session,
    no restart, independent of the global cfg.dev_seed flag. Sets the request-scoped contextvar."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or cfg.dev_seed:
            return await self.app(scope, receive, send)
        token = seed_preview.set(Request(scope).cookies.get("seed_preview") == "1")
        try:
            await self.app(scope, receive, send)
        finally:
            seed_preview.reset(token)


app.add_middleware(SeedPreviewMiddleware)
app.add_middleware(SessionMiddleware, secret_key=cfg.session_secret, https_only=True, same_site="lax")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")
TZ = ZoneInfo(cfg.timezone)
LID = cfg.league_id
DRAFT_HOUR = 20  # 8 PM ET, matches cfg.draft_time_label

NAV = [("home", "/", "League"), ("players", "/draftboard", "Players"),
       ("schedule", "/schedule", "Schedule"), ("games", "/games", "Games")]


def _player_subtabs(active: str):
    return [{"label": "Rankings", "href": "/draftboard", "current": active == "rankings"},
            {"label": "Draft", "href": "/draft", "current": active == "draft"},
            {"label": "Free Agents", "href": "/freeagents", "current": active == "freeagents"}]


def _gamble_subtabs(active: str):
    tabs = []
    if cfg.enable_betting:
        tabs.append({"label": "My Matchups", "href": "/bets", "current": active == "bets"})
    if cfg.enable_h2h_betting:
        tabs.append({"label": "NFL Games", "href": "/h2h", "current": active == "h2h"})
    return tabs
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
    nav_items = [{"href": h, "label": lbl, "current": k == active} for k, h, lbl in NAV]
    if cfg.enable_analysis:  # Insights tab only exists when the analysis flag is on
        nav_items.append({"href": "/insights", "label": "Insights", "current": active == "insights"})
    if cfg.enable_betting or cfg.enable_h2h_betting:  # single Gamble tab for both betting features
        nav_items.append({"href": "/bets" if cfg.enable_betting else "/h2h",
                          "label": "Gamble", "current": active == "gamble"})
    return {
        "request": request,
        "features": {"betting": cfg.enable_betting, "h2h": cfg.enable_h2h_betting,
                     "analysis": cfg.enable_analysis},
        "oauth_enabled": cfg.oauth_enabled,
        "seed_on": cfg.dev_seed or request.cookies.get("seed_preview") == "1",
        "logged_in": bool(request.session.get("discord_id")),
        "me_roster_id": me,
        "my_team_href": f"/team/{me}" if me else None,
        "nav_items": nav_items,
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


@app.get("/toggle-seed")
async def toggle_seed(request: Request):
    on = request.cookies.get("seed_preview") == "1"
    # Redirect back to the referring page, but only its local path — never an
    # attacker-supplied host (open-redirect guard; also proxy-safe behind Caddy).
    ref = urlparse(request.headers.get("referer") or "/")
    target = ref.path if ref.path.startswith("/") else "/"
    if ref.query:
        target += "?" + ref.query
    resp = RedirectResponse(target, status_code=303)
    if on:
        resp.delete_cookie("seed_preview")
    else:
        resp.set_cookie("seed_preview", "1", max_age=86400, samesite="lax")
    return resp


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
    league = await get_league(LID)
    ctx["playoff_teams"] = (league.get("settings") or {}).get("playoff_teams", 6)

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
                 pos: str | None, exclude: set | None, subtab: str | None = None):
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
    ctx["board_note"] = f"{len(rows)} players · {season} PPR"
    ctx["pos_chips"] = [{"label": "All", "href": base_href, "current": pos is None}] + [
        {"label": p, "href": f"{base_href}?pos={p}", "current": pos == p}
        for p in ("QB", "RB", "WR", "TE", "K", "DEF")]
    if subtab:
        ctx["subtabs"] = _player_subtabs(subtab)
    return templates.TemplateResponse(request, "board.html", ctx)


@app.get("/draftboard", response_class=HTMLResponse)
async def draftboard(request: Request, pos: str | None = None):
    return await _board(request, "players", "Players", "/draftboard", pos, exclude=None, subtab="rankings")


@app.get("/draft", response_class=HTMLResponse)
async def draft(request: Request):
    ctx = await _base_ctx(request, "players")
    league = await get_league(LID)
    picks = []
    drafts = await get_drafts(LID)
    did = (drafts[0].get("draft_id") if drafts else None) or league.get("draft_id")
    if did:
        picks = await get_draft_picks(did)
    ctx["rounds"] = []
    if picks:
        users, rosters, players = await get_users(LID), await get_rosters(LID), await get_players()
        ctx["rounds"] = views.draft_results(picks, users, rosters, players)
    ctx["subtabs"] = _player_subtabs("draft")
    return templates.TemplateResponse(request, "draft.html", ctx)


@app.get("/freeagents", response_class=HTMLResponse)
async def freeagents(request: Request, pos: str | None = None):
    rosters = await get_rosters(LID)
    rostered = {pid for r in rosters for pid in (r.get("players") or []) if pid}
    return await _board(request, "players", "Free Agents", "/freeagents", pos, exclude=rostered, subtab="freeagents")


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
    real = False
    if pid not in players:  # e.g. a real NFL player linked from Games while in preview mode
        players = await _real(get_players)
        real = True
    p = players.get(pid)
    if p is None:
        return RedirectResponse("/draftboard")
    ctx = await _base_ctx(request, "")
    season = "2025"
    fetch_stats = (lambda s: _real(get_player_stats, s)) if real else get_player_stats
    st = (await fetch_stats(season)).get(pid, {})
    if not st:
        season = "2024"
        st = (await fetch_stats(season)).get(pid, {})
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
    ctx["recent_games"] = []
    if espn_id:
        try:
            gl = await espn.gamelog(espn_id)
            scoring = (await get_league(LID)).get("scoring_settings") or {}
            ctx["recent_games"] = espn.game_log(gl, scoring)
        except Exception:
            pass
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
                         "a": entry_a, "b": entry_b,
                         "href": f"/matchup/{week}/{g['mid']}" if g.get("mid") is not None else None})

    ctx["matchups"] = matchups
    ctx["week_has_games"] = bool(matchups)
    ctx["week_empty"] = not matchups
    ctx["week_heading"] = f"Week {week}"
    ctx["weeks"] = [{"href": f"/schedule?week={w}", "label": str(w), "current": w == week} for w in range(1, 19)]
    ctx["empty_week_title"] = f"No matchups for Week {week}"
    ctx["empty_week_body"] = ("Scores appear here once the season starts."
                              + (f" Draft is {ctx['draft_date_label']}." if ctx["is_pre"] else ""))
    return templates.TemplateResponse(request, "schedule.html", ctx)


@app.get("/games", response_class=HTMLResponse)
async def games_page(request: Request, week: int | None = None):
    ctx = await _base_ctx(request, "games")
    wk = week if week and 1 <= week <= 18 else 1
    try:
        data = espn.games(await espn.scoreboard(year=2025, week=wk))
    except Exception:
        data = {"games": [], "week": wk}
    ctx["games"] = data["games"]
    ctx["nfl_week"] = data["week"] or wk
    ctx["weeks"] = [{"href": f"/games?week={w}", "label": str(w), "current": w == wk} for w in range(1, 19)]
    return templates.TemplateResponse(request, "games.html", ctx)


async def _real(fn, *args):
    """Run a Sleeper fetch with the preview-seed override forced off (real data)."""
    token = seed_preview.set(False)
    try:
        return await fn(*args)
    finally:
        seed_preview.reset(token)


@app.get("/game/{eid}", response_class=HTMLResponse)
async def game_page(request: Request, eid: str, week: int | None = None, pos: str | None = None):
    ctx = await _base_ctx(request, "games")
    try:
        detail = espn.detail(await espn.summary(eid))
    except Exception:
        detail = None
    if detail is None:
        return RedirectResponse("/games")
    wk = week if week and 1 <= week <= 18 else 1
    # real NFL production (bypass the preview seed); franchise ownership is seed-aware
    real_players = await _real(get_players)
    real_wk = await _real(get_week_stats, "2025", wk)
    users, rosters, league = await get_users(LID), await get_rosters(LID), await get_league(LID)
    groups = views.game_players(detail["away"]["abbr"], detail["home"]["abbr"], real_wk,
                                real_players, rosters, users, league.get("scoring_settings") or {})
    ctx["g"] = detail
    ctx["week"] = wk
    pos = pos if pos in views.FANTASY_POS else None
    ctx["table_pos"] = pos
    if pos:
        flat = sorted((r for r in groups["away"] + groups["home"] if r["pos"] == pos),
                      key=lambda r: r["pts"], reverse=True)
        ctx["table"] = views.pos_table(flat, pos)
    else:
        ctx["groups"] = groups
    base = f"/game/{eid}?week={wk}"
    ctx["pos_chips"] = [{"label": "All", "href": base, "current": pos is None}] + [
        {"label": p, "href": f"{base}&pos={p}", "current": pos == p} for p in ("QB", "RB", "WR", "TE", "K", "DEF")]
    return templates.TemplateResponse(request, "game.html", ctx)


@app.get("/matchup/{week}/{mid}", response_class=HTMLResponse)
async def matchup(request: Request, week: int, mid: int):
    users, rosters, league = await get_users(LID), await get_rosters(LID), await get_league(LID)
    players = await get_players()
    slots = [p for p in league.get("roster_positions", []) if p != "BN"]
    week_stats = await get_week_stats(league.get("season", "2025"), week)
    detail = views.matchup_detail(await get_matchups(LID, week), mid, rosters, users, players, slots, week_stats)
    if detail is None:
        return RedirectResponse(f"/schedule?week={week}")
    ctx = await _base_ctx(request, "schedule")
    ctx.update(detail=detail, week=week, back=f"/schedule?week={week}")
    return templates.TemplateResponse(request, "matchup.html", ctx)


@app.get("/insights", response_class=HTMLResponse)
async def insights(request: Request):
    if not cfg.enable_analysis:
        return RedirectResponse("/")
    ctx = await _base_ctx(request, "insights")
    users, rosters = await get_users(LID), await get_rosters(LID)
    state = await get_nfl_state()
    current = state.get("week") or 1
    weeks = []
    for wk in range(1, current + 1):
        m = await get_matchups(LID, wk)
        if m:  # only weeks that actually have matchup data (played)
            weeks.append(m)
    rows = views.luck_table(weeks, users, rosters)
    ctx["insights"] = rows
    ctx["has_games"] = bool(rows)
    ctx["weeks_played"] = len(weeks)
    ctx["through_label"] = (f"{len(weeks)} weeks played" if len(weeks) != 1 else "1 week played")
    return templates.TemplateResponse(request, "insights.html", ctx)


@app.get("/bets", response_class=HTMLResponse)
async def bets(request: Request):
    if not cfg.enable_betting:
        return RedirectResponse("/")
    ctx = await _base_ctx(request, "gamble")
    ctx["subtabs"] = _gamble_subtabs("bets")
    users, rosters = await get_users(LID), await get_rosters(LID)
    by_id = {u["user_id"]: u for u in users}
    owner_of = {r["roster_id"]: by_id.get(r.get("owner_id")) for r in rosters if r.get("owner_id")}
    state = await get_nfl_state()
    current = state.get("week") or 1

    # Settled results across every played week (matchup_result is None until a game is decided).
    results, cur_week = {}, []
    for wk in range(1, current + 1):
        m = await get_matchups(LID, wk)
        if wk == current:
            cur_week = m
        for rid in owner_of:
            res = betting.matchup_result(m, rid)
            if res:
                results[(rid, wk)] = res
    all_bets = betting.all_bets()
    settled = betting.settle(all_bets, results)
    bal = betting.balances(settled)

    standings = [{"roster_id": rid, "team": views.team_name(u), "avatar": views.avatar_url(u),
                  "balance": bal.get(rid, betting.STARTING_BALANCE)} for rid, u in owner_of.items()]
    standings.sort(key=lambda x: x["balance"], reverse=True)
    for i, row in enumerate(standings, 1):
        row["rank"] = i
    ctx["standings"] = standings
    ctx["starting_balance"] = betting.STARTING_BALANCE

    # The logged-in member's current-week matchup + whether a stake can be placed.
    me = ctx["me_roster_id"]
    ctx["current_week"] = current
    ctx["my_matchup"] = None
    ctx["can_bet"] = False
    if me:
        has_open = any(b["roster_id"] == me and b["week"] == current for b in all_bets)
        groups: dict = {}
        for m in cur_week:
            groups.setdefault(m.get("matchup_id"), []).append(m)
        for entries in groups.values():
            ids = [e["roster_id"] for e in entries]
            if me in ids and len(entries) == 2:
                opp = next(rid for rid in ids if rid != me)
                ctx["my_matchup"] = {"me_team": views.team_name(owner_of.get(me)),
                                     "opp_team": views.team_name(owner_of.get(opp))}
                break
        played = betting.matchup_result(cur_week, me)
        ctx["my_bet"] = next((b for b in all_bets if b["roster_id"] == me and b["week"] == current), None)
        ctx["can_bet"] = played is None and not has_open and bool(ctx["my_matchup"])

    recent = [{"team": views.team_name(owner_of.get(b["roster_id"])), "week": b["week"],
               "stake": b["stake"], "status": b["status"], "payout": b["payout"]}
              for b in settled if b["status"] != "open"][:12]
    ctx["recent"] = recent
    return templates.TemplateResponse(request, "bets.html", ctx)


@app.post("/bets")
async def place_bet(request: Request):
    if not cfg.enable_betting:
        return RedirectResponse("/")
    me = await _me_roster_id(request)  # a member may only ever bet on their OWN roster
    if me is None:
        return RedirectResponse("/login" if cfg.oauth_enabled else "/")
    form = parse_qs((await request.body()).decode())  # urlencoded; avoids python-multipart dep
    try:
        stake, week = int(form["stake"][0]), int(form["week"][0])
    except (KeyError, IndexError, TypeError, ValueError):
        return RedirectResponse("/bets", 303)
    state = await get_nfl_state()
    current = state.get("week") or 1
    if stake <= 0 or week != current:
        return RedirectResponse("/bets", 303)
    if betting.matchup_result(await get_matchups(LID, current), me) is not None:
        return RedirectResponse("/bets", 303)  # week already decided — no wagering
    try:
        betting.place_bet(me, current, stake)
    except ValueError:
        pass  # duplicate / invalid — fall through to a clean redirect
    return RedirectResponse("/bets", 303)


@app.get("/h2h", response_class=HTMLResponse)
async def h2h_page(request: Request):
    if not cfg.enable_h2h_betting:
        return RedirectResponse("/")
    ctx = await _base_ctx(request, "gamble")
    ctx["subtabs"] = _gamble_subtabs("h2h")
    users, rosters = await get_users(LID), await get_rosters(LID)
    by_id = {u["user_id"]: u for u in users}
    owner_of = {r["roster_id"]: by_id.get(r.get("owner_id")) for r in rosters if r.get("owner_id")}

    board = h2h.games(await odds.get_nfl_odds())
    ctx["games"] = board
    game_teams = {g["game_id"]: (g["home"], g["away"]) for g in board}

    def team_name(rid):
        return views.team_name(owner_of.get(rid)) if rid is not None else None

    all_wagers = h2h.all_wagers()
    for w in all_wagers:
        w["proposer_team"] = team_name(w["proposer"])
        w["acceptor_team"] = team_name(w["acceptor"])
    ctx["open_wagers"] = [w for w in all_wagers if w["acceptor"] is None]
    ctx["matched_wagers"] = [w for w in all_wagers if w["acceptor"] is not None]

    # Settlement of live NFL results is out of scope this round → wagers stay open/matched.
    settled = h2h.settle(all_wagers, {})
    net = h2h.net_ledger(settled)
    ledger = [{"roster_id": rid, "team": views.team_name(u), "avatar": views.avatar_url(u),
               "net": net.get(rid, 0)} for rid, u in owner_of.items()]
    ledger.sort(key=lambda x: x["net"], reverse=True)
    for i, row in enumerate(ledger, 1):
        row["rank"] = i
    ctx["ledger"] = ledger
    ctx["can_bet"] = bool(ctx["me_roster_id"])
    return templates.TemplateResponse(request, "h2h.html", ctx)


@app.post("/h2h/propose")
async def h2h_propose(request: Request):
    if not cfg.enable_h2h_betting:
        return RedirectResponse("/")
    me = await _me_roster_id(request)
    if me is None:
        return RedirectResponse("/login" if cfg.oauth_enabled else "/")
    form = parse_qs((await request.body()).decode())  # urlencoded; avoids python-multipart dep
    try:
        game_id, side = form["game_id"][0], form["side"][0]
        price, stake = int(form["price"][0]), int(form["stake"][0])
    except (KeyError, IndexError, TypeError, ValueError):
        return RedirectResponse("/h2h", 303)
    board = h2h.games(await odds.get_nfl_odds())
    game = next((g for g in board if g["game_id"] == game_id), None)
    if game is None or side not in (game["home"], game["away"]):
        return RedirectResponse("/h2h", 303)  # unknown game / side — reject cleanly
    try:
        h2h.propose(me, game_id, side, price, stake)
    except ValueError:
        pass  # invalid stake — fall through to a clean redirect
    return RedirectResponse("/h2h", 303)


@app.post("/h2h/accept")
async def h2h_accept(request: Request):
    if not cfg.enable_h2h_betting:
        return RedirectResponse("/")
    me = await _me_roster_id(request)
    if me is None:
        return RedirectResponse("/login" if cfg.oauth_enabled else "/")
    form = parse_qs((await request.body()).decode())
    try:
        wager_id = int(form["wager_id"][0])
    except (KeyError, IndexError, TypeError, ValueError):
        return RedirectResponse("/h2h", 303)
    try:
        h2h.accept(wager_id, me)
    except ValueError:
        pass  # already taken / self-accept / missing — reject cleanly
    return RedirectResponse("/h2h", 303)
