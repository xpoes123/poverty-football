"""Poverty Franchises — read-only league portal (nfl.djiang.xyz).

Thin FastAPI layer: each route gathers Sleeper data (cached) → shapes it via views.py
→ renders the designed Jinja templates. No writes, no auth.
"""

import datetime as dt
import secrets
from urllib.parse import parse_qs, urlencode
from zoneinfo import ZoneInfo

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

import analytics
import betting
import h2h
import members
import odds
import espn
import results
import views
from config import LEAGUES, LEAGUE_IDS, cfg, league_cfg
from sleeper import (
    active_league,
    get_draft_picks,
    get_drafts,
    get_league,
    get_matchups,
    get_nfl_state,
    get_player_stats,
    get_players,
    get_projections,
    get_week_stats,
    get_rosters,
    get_transactions,
    get_trending_adds,
    get_users,
    resolve_user_id,
)

app = FastAPI(title="Poverty Franchises")


class ActiveLeagueMiddleware:
    """Sets the request-scoped active league from the `league` cookie (validated against
    the known list; unknown/absent → the default). Every Sleeper fetch reads it via lid()."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        cookie = Request(scope).cookies.get("league")
        token = active_league.set(cookie if cookie in LEAGUE_IDS else cfg.league_id)
        try:
            await self.app(scope, receive, send)
        finally:
            active_league.reset(token)


app.add_middleware(ActiveLeagueMiddleware)
app.add_middleware(SessionMiddleware, secret_key=cfg.session_secret, https_only=True, same_site="lax")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")
TZ = ZoneInfo(cfg.timezone)


def lid() -> str:
    """The league the current request is viewing (see ActiveLeagueMiddleware)."""
    return active_league.get()


async def _season() -> str:
    """Current NFL season year (e.g. '2026'), from Sleeper state."""
    return (await get_nfl_state()).get("season") or str(dt.datetime.now(TZ).year)


async def _stats_season(fetch):
    """(season, stats) for the current season, falling back to last season when the
    current one has no data yet (early weeks). `fetch` is get_player_stats-like."""
    season = await _season()
    stats = await fetch(season)
    if not stats:
        season = str(int(season) - 1)
        stats = await fetch(season)
    return season, stats


DRAFT_HOUR = 20  # 8 PM ET, matches cfg.draft_time_label

NAV = [("home", "/", "League"), ("players", "/draftboard", "Players"),
       ("schedule", "/schedule", "Schedule"), ("games", "/games", "Games")]


def _player_subtabs(active: str):
    return [{"label": "Rankings", "href": "/draftboard", "current": active == "rankings"},
            {"label": "Draft", "href": "/draft", "current": active == "draft"},
            {"label": "Free Agents", "href": "/freeagents", "current": active == "freeagents"},
            {"label": "Trade", "href": "/trade", "current": active == "trade"}]


def _gamble_subtabs(active: str):
    tabs = []
    if cfg.enable_betting:
        tabs.append({"label": "My Matchups", "href": "/bets", "current": active == "bets"})
    if cfg.enable_h2h_betting:
        tabs.append({"label": "NFL Games", "href": "/h2h", "current": active == "h2h"})
    return tabs


def _insights_subtabs(active: str):
    return [{"label": "Luck", "href": "/insights", "current": active == "luck"},
            {"label": "Playoff Odds", "href": "/playoffs", "current": active == "playoffs"},
            {"label": "Power", "href": "/power", "current": active == "power"},
            {"label": "Records", "href": "/records", "current": active == "records"},
            {"label": "Recaps", "href": "/recaps", "current": active == "recaps"},
            {"label": "Rivalry", "href": "/rivalry", "current": active == "rivalry"}]


async def _played_weeks() -> list[tuple]:
    """(week_no, matchups) for the active league's completed, scored weeks (skips in-progress)."""
    current = (await get_nfl_state()).get("week") or 1
    weeks = []
    for wk in range(1, current):
        m = await get_matchups(lid(), wk)
        if m and any((e.get("points") or 0) > 0 for e in m):  # scored → actually played
            weeks.append((wk, m))
    return weeks
TX_KINDS = {"trade": "Trade", "waiver": "Waiver", "free_agent": "Add"}
STATUS_LABEL = {"pre_draft": "Pre-Draft Season", "drafting": "Draft Underway",
                "in_season": "Regular Season", "complete": "Season Complete"}


def _fmt(pts) -> str:
    return f"{(pts or 0):.1f}"


def _discord_map() -> dict:
    """discord_id -> Sleeper handle, unioned across every league's member file (a person's
    handle is the same in any league, so 'My Team' resolves on whichever league they're in)."""
    out = {}
    for lg in LEAGUES:
        for m in members.load(lg["id"]):
            if m.get("discord_id"):
                out[m["discord_id"]] = m["sleeper"]
    return out


def _discord_names() -> dict:
    """string discord_id -> member name, unioned across all leagues (for labelling analytics)."""
    out = {}
    for lg in LEAGUES:
        for m in members.load(lg["id"]):
            if m.get("discord_id"):
                out[str(m["discord_id"])] = m.get("name", m["sleeper"])
    return out


async def _dues(users, rosters, league_id: str) -> list[dict]:
    """Per-member dues status for a league, enriched with franchise avatar + team link when
    the member has joined. Empty if that league has no member file yet."""
    by_uid = {u["user_id"]: u for u in users}
    roster_of = {r.get("owner_id"): r for r in rosters if r.get("owner_id")}
    rows = []
    for m in members.load(league_id):
        uid = await resolve_user_id(m["sleeper"])
        user, roster = by_uid.get(uid), roster_of.get(uid)
        rows.append({
            "name": m.get("name", m["sleeper"]),  # the person, as David tracks them
            "paid": bool(m.get("paid")),
            "joined": user is not None,
            "avatar": views.avatar_url(user),
            "href": f"/team/{roster['roster_id']}" if roster else None,
        })
    rows.sort(key=lambda r: (r["paid"], r["name"].lower()))  # unpaid first, then alphabetical
    return rows


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
    for r in await get_rosters(lid()):
        if r.get("owner_id") == uid or uid in (r.get("co_owners") or []):
            return r["roster_id"]
    return None


async def _base_ctx(request: Request, active: str) -> dict:
    league = await get_league(lid())
    rosters = await get_rosters(lid())
    status, season = league["status"], league["season"]
    is_pre = status in ("pre_draft", "drafting")
    teams_in = sum(1 for r in rosters if r.get("owner_id"))
    total = league["total_rosters"]
    lg = league_cfg(lid())
    meta = members.load_meta(lid())  # admin-editable settings override the config defaults
    try:
        d = dt.date.fromisoformat(meta["draft_date"]) if meta.get("draft_date") else lg.get("draft_date")
    except ValueError:
        d = lg.get("draft_date")
    time_label = meta.get("draft_time_label") or lg.get("draft_time_label") or "TBD"
    draft_line = f"{d:%b %-d}, {time_label}" if d else "To be announced"
    target = dt.datetime.combine(d, dt.time(DRAFT_HOUR), tzinfo=TZ) if d else None
    upcoming = target and target > dt.datetime.now(TZ)
    me = await _me_roster_id(request)
    did = request.session.get("discord_id")
    is_admin = str(did) == cfg.admin_discord_id
    # record the page view (never let analytics break a render); skip the admin's own analytics page
    if request.url.path != "/analytics":
        analytics.record(request.url.path, did)
    nav_items = [{"href": h, "label": lbl, "current": k == active} for k, h, lbl in NAV]
    if cfg.enable_analysis:  # Insights tab only exists when the analysis flag is on
        nav_items.append({"href": "/insights", "label": "Insights", "current": active == "insights"})
    if cfg.enable_betting or cfg.enable_h2h_betting:  # single Gamble tab for both betting features
        nav_items.append({"href": "/bets" if cfg.enable_betting else "/h2h",
                          "label": "Gamble", "current": active == "gamble"})
    if is_admin:  # private admin tabs, admin only
        nav_items.append({"href": "/admin", "label": "Admin", "current": active == "admin"})
        nav_items.append({"href": "/analytics", "label": "Analytics", "current": active == "analytics"})
    return {
        "request": request,
        "features": {"betting": cfg.enable_betting, "h2h": cfg.enable_h2h_betting,
                     "analysis": cfg.enable_analysis},
        "oauth_enabled": cfg.oauth_enabled,
        "leagues": LEAGUES,
        "current_league_id": lid(),
        "league_name": league.get("name") or "Fantasy League",
        "team_total": total,
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
        "draft_when": f"{d:%b %-d, %Y} · {time_label}" if d else "",
        "join_url": lg.get("join_url"),
        "dues_amount": meta.get("dues_amount"),
        "pay_url": meta.get("pay_url"),
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
        raw += await get_transactions(lid(), wk)
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


@app.get("/switch-league/{league_id}")
async def switch_league(league_id: str):
    # Redirect home (not back): pages are league-scoped, so a deep link may not exist
    # in the newly-selected league. Ignore unknown ids rather than trust the input.
    resp = RedirectResponse("/", status_code=303)
    if league_id in LEAGUE_IDS:
        resp.set_cookie("league", league_id, max_age=31536000, samesite="lax")
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


def _parse_iso(s: str | None) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(TZ)
    except (ValueError, AttributeError):
        return None


async def _next_kickoff(current: int) -> str | None:
    """Next NFL kickoff (ISO, ET). Returns the soonest future game; in preview every game is
    historical, so the earliest upcoming-week game is rolled forward to its next occurrence."""
    now = dt.datetime.now(TZ)

    async def week_dates(wk: int) -> list[dt.datetime]:
        if not (1 <= wk <= 18):
            return []
        try:
            data = espn.games(await espn.scoreboard(year=int(await _season()), week=wk))
        except Exception:
            return []
        return sorted(d for g in data["games"] if (d := _parse_iso(g.get("date"))))

    cur = await week_dates(current)
    future = [d for d in cur if d > now]
    if future:
        return future[0].isoformat()          # real: kickoff still to come this week
    pool = await week_dates(current + 1) or cur
    if not pool:
        return None
    future = [d for d in pool if d > now]
    if future:
        return future[0].isoformat()          # real: first kickoff of next week
    d = pool[0]                               # preview: roll a historical kickoff forward
    while d <= now:
        d += dt.timedelta(days=7)
    return d.isoformat()


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    ctx = await _base_ctx(request, "home")
    users, rosters = await get_users(lid()), await get_rosters(lid())
    league = await get_league(lid())
    ctx["playoff_teams"] = (league.get("settings") or {}).get("playoff_teams", 6)

    ctx["links"] = [{"label": "League Rules", "href": "/rules", "external": False},
                    {"label": "Open in Sleeper", "href": f"https://sleeper.com/leagues/{lid()}", "external": True}]
    if ctx.get("join_url"):  # only leagues with a known invite link
        ctx["links"].insert(0, {"label": "Join the League", "href": ctx["join_url"], "external": True})
    ctx["rows"] = _standings_rows(users, rosters)
    ctx["tx_feed"] = await _tx_feed(users, rosters) if ctx["has_season"] else []
    # Dues come from the league's member file (expected[.<id>].toml) — shown for any league that has one.
    ctx["dues"] = await _dues(users, rosters, lid())
    ctx["dues_paid"] = sum(1 for d in ctx["dues"] if d["paid"])
    amount = ctx.get("dues_amount")
    if amount and ctx["dues"]:
        unpaid = len(ctx["dues"]) - ctx["dues_paid"]
        ctx["dues_totals"] = {"amount": amount, "collected": ctx["dues_paid"] * amount,
                              "outstanding": unpaid * amount, "total": len(ctx["dues"]) * amount}
    countdown = None
    if ctx["is_pre"]:
        ctx["table_title"] = "Franchises"
        ctx["through_label"] = f"{ctx['seated_line']} seated"
        if ctx["draft_target"]:  # real pre-draft: count down to the draft
            countdown = {"target": ctx["draft_target"], "label": "Draft begins in",
                         "when": ctx["draft_when"], "done": "It's draft day"}
    else:
        current = (await get_nfl_state()).get("week") or 1
        ctx["table_title"] = "Standings"
        ctx["through_label"] = f"Through Week {current}"
        # live scores of this week's matchups
        scores = views.scoreboard(await get_matchups(lid(), current), rosters, users)
        ctx["week_scores"] = [{"a": g["sides"][0], "b": g["sides"][1] if len(g["sides"]) > 1 else None,
                               "winner": g["winner"],
                               "href": f"/matchup/{current}/{g['mid']}" if g.get("mid") is not None else None}
                              for g in scores]
        ctx["scores_week"] = current
        kick = await _next_kickoff(current)  # count down to the next kickoff
        if kick:
            countdown = {"target": kick, "label": "Next kickoff", "when": views.kick_label(kick, full=True),
                         "done": "Kicking off"}
    ctx["countdown"] = countdown
    return templates.TemplateResponse(request, "home.html", ctx)


@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    if str(request.session.get("discord_id")) != cfg.admin_discord_id:
        return RedirectResponse("/")  # admin only
    ctx = await _base_ctx(request, "admin")
    users = await get_users(lid())
    joined = {u["user_id"] for u in users}  # who's actually in the Sleeper league
    rows, linked_uids = [], set()
    for i, m in enumerate(members.load(lid())):
        uid = await resolve_user_id(m["sleeper"])
        if uid:
            linked_uids.add(uid)
        rows.append({"i": i, "name": m.get("name", ""), "sleeper": m.get("sleeper", ""),
                     "discord_id": m.get("discord_id") or "", "paid": bool(m.get("paid")),
                     "joined": bool(uid) and uid in joined})
    ctx["members"] = rows
    ctx["blank_idxs"] = list(range(len(rows), len(rows) + 3))  # spare rows for adding members
    ctx["meta"] = members.load_meta(lid())
    # Sleeper teams in the league with no member row yet — autofill hints (handle = login username).
    ctx["unlinked"] = sorted(
        ({"handle": u.get("display_name") or "?", "team": views.team_name(u)}
         for u in users if u.get("user_id") not in linked_uids),
        key=lambda x: x["handle"].lower())
    return templates.TemplateResponse(request, "admin.html", ctx)


@app.post("/admin")
async def admin_save(request: Request):
    if str(request.session.get("discord_id")) != cfg.admin_discord_id:
        return RedirectResponse("/")
    # keep_blank_values keeps the parallel arrays aligned; paid/remove are keyed by row index.
    form = parse_qs((await request.body()).decode(), keep_blank_values=True)
    names, sleepers, dids = form.get("name", []), form.get("sleeper", []), form.get("discord_id", [])
    paid, remove = set(form.get("paid", [])), set(form.get("remove", []))
    out = []
    for i, (name, sleeper, did) in enumerate(zip(names, sleepers, dids)):
        sleeper, did = sleeper.strip(), did.strip()
        if not sleeper or str(i) in remove:  # blank sleeper or removed row -> dropped
            continue
        m = {"name": name.strip() or sleeper, "sleeper": sleeper, "paid": str(i) in paid}
        if did.isdigit():
            m["discord_id"] = int(did)
        out.append(m)
    meta = {k: (form.get(k, [""])[0].strip() or None) for k in ("pay_url", "draft_date", "draft_time_label")}
    amt = form.get("dues_amount", [""])[0].strip()
    meta["dues_amount"] = amt if amt else None
    members.save(out, meta, lid())
    return RedirectResponse("/admin", 303)


@app.get("/analytics", response_class=HTMLResponse)
async def analytics_page(request: Request):
    if str(request.session.get("discord_id")) != cfg.admin_discord_id:
        return RedirectResponse("/")  # admin only
    ctx = await _base_ctx(request, "analytics")
    stats = analytics.summary(_discord_names())

    def fmt(ts):
        return dt.datetime.fromtimestamp(ts, TZ).strftime("%b %-d, %-I:%M %p") if ts else "—"

    for v in stats["visitors"]:
        v["last_str"] = fmt(v["last"])
    for r in stats["recent"]:
        r["when"] = fmt(r["ts"])
    ctx["stats"] = stats
    ctx["since_str"] = fmt(stats["since"])
    return templates.TemplateResponse(request, "analytics.html", ctx)


async def _board(request: Request, active: str, heading: str, base_href: str,
                 pos: str | None, exclude: set | None, subtab: str | None = None,
                 extra: dict | None = None):
    ctx = await _base_ctx(request, active)
    players = await get_players()
    season, stats = await _stats_season(get_player_stats)
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
    if extra:
        ctx.update(extra)
    return templates.TemplateResponse(request, "board.html", ctx)


async def _next_week_proj() -> dict:
    """pid -> projected fantasy points for the active league's current NFL week (its scoring)."""
    week = (await get_nfl_state()).get("week") or 1
    league = await get_league(lid())
    season = league.get("season") or await _season()
    scoring = league.get("scoring_settings") or {}
    return {pid: views.fantasy_points(st, scoring) for pid, st in (await get_projections(season, week)).items()}


@app.get("/draftboard", response_class=HTMLResponse)
async def draftboard(request: Request, pos: str | None = None):
    return await _board(request, "players", "Players", "/draftboard", pos, exclude=None, subtab="rankings")


@app.get("/draft", response_class=HTMLResponse)
async def draft(request: Request):
    ctx = await _base_ctx(request, "players")
    league = await get_league(lid())
    picks = []
    drafts = await get_drafts(lid())
    did = (drafts[0].get("draft_id") if drafts else None) or league.get("draft_id")
    if did:
        picks = await get_draft_picks(did)
    ctx["rounds"] = []
    if picks:
        users, rosters, players = await get_users(lid()), await get_rosters(lid()), await get_players()
        ctx["rounds"] = views.draft_results(picks, users, rosters, players)
    ctx["subtabs"] = _player_subtabs("draft")
    return templates.TemplateResponse(request, "draft.html", ctx)


@app.get("/freeagents", response_class=HTMLResponse)
async def freeagents(request: Request, pos: str | None = None):
    rosters = await get_rosters(lid())
    rostered = {pid for r in rosters for pid in (r.get("players") or []) if pid}
    players = await get_players()
    proj = await _next_week_proj()
    trending = []
    for t in await get_trending_adds(40):
        pid = str(t.get("player_id"))
        p = players.get(pid)
        if pid in rostered or not p or p.get("position") not in views.FANTASY_POS:
            continue
        pl = views.player_line(pid, players)
        trending.append({"pid": pid, "name": pl["name"], "pos": pl["pos"], "team": pl["team"] or "FA",
                         "proj": round(proj.get(pid, 0.0), 1), "adds": t.get("count", 0)})
        if len(trending) >= 8:
            break
    return await _board(request, "players", "Free Agents", "/freeagents", pos,
                        exclude=rostered, subtab="freeagents", extra={"trending": trending})


@app.get("/trade", response_class=HTMLResponse)
async def trade(request: Request, a: int | None = None, b: int | None = None):
    ctx = await _base_ctx(request, "players")
    ctx["subtabs"] = _player_subtabs("trade")
    users, rosters = await get_users(lid()), await get_rosters(lid())
    owned = [r for r in rosters if r.get("owner_id")]
    if not owned:
        ctx["teams"] = []
        return templates.TemplateResponse(request, "trade.html", ctx)
    by_uid = {u["user_id"]: u for u in users}
    picker = sorted(({"roster_id": r["roster_id"], "name": views.team_name(by_uid.get(r["owner_id"]))}
                     for r in owned), key=lambda x: x["name"].lower())
    ids = {r["roster_id"] for r in owned}
    a = a if a in ids else (ctx.get("me_roster_id") if ctx.get("me_roster_id") in ids else picker[0]["roster_id"])
    b = b if b in ids else next((p["roster_id"] for p in picker if p["roster_id"] != a), a)
    players = await get_players()
    proj = await _next_week_proj()
    ra = next(r for r in owned if r["roster_id"] == a)
    rb = next(r for r in owned if r["roster_id"] == b)
    ctx["teams"] = picker
    ctx["a_id"], ctx["b_id"] = a, b
    ctx["a_name"] = views.team_name(by_uid.get(ra["owner_id"]))
    ctx["b_name"] = views.team_name(by_uid.get(rb["owner_id"]))
    ctx["side_a"] = views.trade_side(ra, players, proj)
    ctx["side_b"] = views.trade_side(rb, players, proj)
    return templates.TemplateResponse(request, "trade.html", ctx)


@app.get("/team/{roster_id}", response_class=HTMLResponse)
async def team_page(request: Request, roster_id: int, view: str = "roster"):
    users, rosters, league = await get_users(lid()), await get_rosters(lid()), await get_league(lid())
    roster = next((r for r in rosters if r["roster_id"] == roster_id and r.get("owner_id")), None)
    if roster is None:
        return RedirectResponse("/")
    view = view if view in ("roster", "schedule") else "roster"
    ctx = await _base_ctx(request, "")
    ctx["view"] = view
    ctx["team_tabs"] = [
        {"label": "Roster", "href": f"/team/{roster_id}", "current": view == "roster"},
        {"label": "Schedule", "href": f"/team/{roster_id}?view=schedule", "current": view == "schedule"},
    ]
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
        if view == "schedule":
            current = (await get_nfl_state()).get("week") or 1
            by_week = {wk: m for wk in range(1, current + 1) if (m := await get_matchups(lid(), wk))}
            ctx["schedule"] = views.team_schedule(by_week, roster_id, rosters, users)
        else:
            scoring = league.get("scoring_settings") or {}
            _, stats = await _stats_season(get_player_stats)
            pos_ranks = views.positional_ranks(players, stats, scoring)
            positions = league.get("roster_positions", [])
            ctx["roster"] = views.roster_view(roster, players, stats, scoring, positions, pos_ranks)
            # Next-week lineup breakdown — only on your OWN team (needs Discord login).
            if ctx.get("me_roster_id") == roster_id:
                week = (await get_nfl_state()).get("week") or 1
                season = league.get("season") or await _season()
                proj_pts = {pid: views.fantasy_points(st, scoring)
                            for pid, st in (await get_projections(season, week)).items()}
                ctx["breakdown"] = views.next_week_breakdown(
                    roster, rosters, players, proj_pts, positions, week)
    return templates.TemplateResponse(request, "team.html", ctx)


@app.get("/player/{pid}", response_class=HTMLResponse)
async def player_profile(request: Request, pid: str):
    players = await get_players()
    p = players.get(pid)
    if p is None:
        return RedirectResponse("/draftboard")
    ctx = await _base_ctx(request, "")
    season, stats = await _stats_season(get_player_stats)
    st = stats.get(pid, {})
    position = p.get("position") or ""
    name = p.get("full_name") or f"{p.get('first_name', '')} {p.get('last_name', '')}".strip() or pid
    espn_id = p.get("espn_id") or await espn.resolve_athlete(name, p.get("team"))
    ctx["player"] = {
        "name": name,
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
    ctx["game_table"] = None
    if espn_id:
        try:
            gl = await espn.gamelog(espn_id, int(await _season()))
            scoring = (await get_league(lid())).get("scoring_settings") or {}
            ctx["game_table"] = espn.game_table(gl, scoring, position)
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
    season, stats = await _stats_season(get_player_stats)
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
    users, rosters = await get_users(lid()), await get_rosters(lid())
    games = views.scoreboard(await get_matchups(lid(), week), rosters, users)
    upcoming = not ctx["is_pre"] and week > current  # fixtures exist but no results yet

    # Win probabilities for the current/future week (not for already-final weeks).
    model = {}
    if not ctx["is_pre"] and week >= current:
        scores, _ = await _season_frame()
        model = views.scoring_model(scores, {r["roster_id"] for r in rosters if r.get("owner_id")})

    matchups = []
    for i, g in enumerate(games):
        sides = g["sides"]
        a = sides[0]
        b = sides[1] if len(sides) > 1 else None
        pa = pb = None
        if model and b:
            pa = views.matchup_win_pct(model[a["roster_id"]], model[b["roster_id"]])
            pb = round(100 - pa, 1)

        def entry(s, is_winner, prob):
            return {"win": "true" if (is_winner and not upcoming) else "false", "avatar": s["avatar"],
                    "initials": views.initials(s["team"]), "name": s["team"],
                    "score": "—" if upcoming else _fmt(s["points"]),
                    "prob": prob}

        entry_a = entry(a, g["winner"] == 0, pa)
        entry_b = (entry(b, g["winner"] == 1, pb) if b else
                   {"win": "false", "avatar": None, "initials": "—", "name": "Bye", "score": "—", "prob": None})
        status = "Upcoming" if upcoming else ("" if ctx["is_pre"] else "Final")
        href = f"/matchup/{week}/{g['mid']}" if g.get("mid") is not None else None
        matchups.append({"slot": f"Match {i + 1}", "status": status,
                         "cta": "Preview" if upcoming else "Breakdown",
                         "a": entry_a, "b": entry_b, "href": href})

    ctx["matchups"] = matchups
    ctx["week_has_games"] = bool(matchups)
    ctx["week_empty"] = not matchups
    ctx["week_heading"] = f"Week {week}"
    ctx["weeks"] = [{"href": f"/schedule?week={w}", "label": str(w), "current": w == week,
                     "done": not ctx["is_pre"] and w <= current} for w in range(1, 19)]
    ctx["empty_week_title"] = f"No matchups for Week {week}"
    ctx["empty_week_body"] = (f"Scores appear here once the season starts. Draft is {ctx['draft_date_label']}."
                              if ctx["is_pre"] else "Matchups for this week aren't set yet.")
    return templates.TemplateResponse(request, "schedule.html", ctx)


@app.get("/games", response_class=HTMLResponse)
async def games_page(request: Request, week: int | None = None):
    ctx = await _base_ctx(request, "games")
    current = (await get_nfl_state()).get("week") or 1
    wk = week if week and 1 <= week <= 18 else current
    try:
        data = espn.games(await espn.scoreboard(year=int(await _season()), week=wk))
    except Exception:
        data = {"games": [], "week": wk}
    games = data["games"]
    upcoming = wk > current
    if upcoming:  # future week — games haven't happened yet; show as scheduled
        for g in games:
            g["away"]["score"] = g["home"]["score"] = None
            g["away"]["winner"] = g["home"]["winner"] = False
            g["status"] = views.kick_label(g.get("date")) or "Scheduled"
    ctx["games"] = games
    ctx["upcoming"] = upcoming
    ctx["nfl_week"] = data["week"] or wk
    ctx["weeks"] = [{"href": f"/games?week={w}", "label": str(w), "current": w == wk,
                     "done": w <= current} for w in range(1, 19)]
    return templates.TemplateResponse(request, "games.html", ctx)


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
    users, rosters, league = await get_users(lid()), await get_rosters(lid()), await get_league(lid())
    scoring = league.get("scoring_settings") or {}
    real_players = await get_players()
    season = league.get("season") or await _season()
    current = (await get_nfl_state()).get("week") or 1
    preview = wk > current  # future week — no results yet, show projections
    if preview:
        stats = await get_projections(season, wk)
        detail["away"]["score"] = detail["home"]["score"] = None
        detail["away"]["winner"] = detail["home"]["winner"] = False
        detail["status"] = views.kick_label(detail.get("date"), full=True) or "Scheduled"
    else:
        stats = await get_week_stats(season, wk)
    groups = views.game_players(detail["away"]["abbr"], detail["home"]["abbr"], stats,
                                real_players, rosters, users, scoring)
    ctx["g"] = detail
    ctx["week"] = wk
    ctx["preview"] = preview
    ctx["unit_note"] = ("Projected points · our scoring" if preview else "Fantasy points · our scoring")
    ctx["espn_url"] = None if preview else f"https://www.espn.com/nfl/game/_/gameId/{eid}"
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
    users, rosters, league = await get_users(lid()), await get_rosters(lid()), await get_league(lid())
    players = await get_players()
    positions = league.get("roster_positions", [])
    season = league.get("season") or await _season()
    matchups = await get_matchups(lid(), week)
    current = (await get_nfl_state()).get("week") or 1
    ctx = await _base_ctx(request, "schedule")
    ctx.update(week=week, back=f"/schedule?week={week}")

    if not ctx["is_pre"] and week > current:  # upcoming game -> projected preview
        projections = await get_projections(season, week)
        try:
            sched_espn = espn.week_schedule(await espn.scoreboard(year=int(season), week=week))
        except Exception:
            sched_espn = {}
        sched = {views.ESPN_TO_SLEEPER_TEAM.get(k, k): v for k, v in sched_espn.items()}
        preview = views.matchup_preview(matchups, mid, rosters, users, players, positions,
                                        projections, sched, league.get("scoring_settings") or {})
        if preview is None:
            return RedirectResponse(f"/schedule?week={week}")
        ctx["p"] = preview
        return templates.TemplateResponse(request, "matchup_preview.html", ctx)

    slots = [p for p in positions if p != "BN"]
    week_stats = await get_week_stats(season, week)
    detail = views.matchup_detail(matchups, mid, rosters, users, players, slots, week_stats)
    if detail is None:
        return RedirectResponse(f"/schedule?week={week}")
    ctx["detail"] = detail
    return templates.TemplateResponse(request, "matchup.html", ctx)


@app.get("/insights", response_class=HTMLResponse)
async def insights(request: Request):
    if not cfg.enable_analysis:
        return RedirectResponse("/")
    ctx = await _base_ctx(request, "insights")
    users, rosters = await get_users(lid()), await get_rosters(lid())
    weeks = [m for _, m in await _played_weeks()]
    rows = views.luck_table(weeks, users, rosters)
    ctx["insights"] = rows
    ctx["has_games"] = bool(rows)
    ctx["weeks_played"] = len(weeks)
    ctx["through_label"] = (f"{len(weeks)} weeks played" if len(weeks) != 1 else "1 week played")
    ctx["subtabs"] = _insights_subtabs("luck")
    return templates.TemplateResponse(request, "insights.html", ctx)


async def _season_frame():
    """(scores_by_team, remaining head-to-heads) for the active league's regular season:
    played weeks feed each team's scoring model; current/future weeks become sim matchups."""
    settings = (await get_league(lid())).get("settings") or {}
    last_reg = (settings.get("playoff_week_start") or 15) - 1
    current = (await get_nfl_state()).get("week") or 1
    scores, remaining = {}, []
    for wk in range(1, last_reg + 1):
        m = await get_matchups(lid(), wk)
        if not m:
            continue
        if wk < current and any((e.get("points") or 0) > 0 for e in m):  # played
            for e in m:
                if e.get("points") is not None:
                    scores.setdefault(e["roster_id"], []).append(round(e["points"], 1))
        else:  # in-progress or future -> simulate
            groups: dict = {}
            for e in m:
                groups.setdefault(e.get("matchup_id"), []).append(e["roster_id"])
            remaining += [(g[0], g[1]) for g in groups.values() if len(g) == 2]
    return scores, remaining


@app.get("/power", response_class=HTMLResponse)
async def power(request: Request):
    if not cfg.enable_analysis:
        return RedirectResponse("/")
    ctx = await _base_ctx(request, "insights")
    users, rosters = await get_users(lid()), await get_rosters(lid())
    weeks = [m for _, m in await _played_weeks()]
    ctx["subtabs"] = _insights_subtabs("power")
    ctx["rankings"] = views.power_rankings(weeks, users, rosters) if weeks else []
    ctx["has_games"] = bool(ctx["rankings"])
    ctx["through_label"] = f"{len(weeks)} week{'' if len(weeks) == 1 else 's'} played"
    return templates.TemplateResponse(request, "power.html", ctx)


@app.get("/records", response_class=HTMLResponse)
async def records(request: Request):
    if not cfg.enable_analysis:
        return RedirectResponse("/")
    ctx = await _base_ctx(request, "insights")
    users, rosters = await get_users(lid()), await get_rosters(lid())
    weeks = await _played_weeks()
    ctx["subtabs"] = _insights_subtabs("records")
    ctx["records"] = views.records_book(weeks, users, rosters) if weeks else {}
    ctx["has_games"] = bool(ctx["records"])
    if weeks:
        wk, ms = weeks[-1]
        players = await get_players()
        ctx["awards"] = views.weekly_awards(wk, ms, users, rosters, players)
        ctx["awards_week"] = wk
    return templates.TemplateResponse(request, "records.html", ctx)


@app.get("/recaps", response_class=HTMLResponse)
async def recaps(request: Request):
    if not cfg.enable_analysis:
        return RedirectResponse("/")
    ctx = await _base_ctx(request, "insights")
    users, rosters = await get_users(lid()), await get_rosters(lid())
    ctx["subtabs"] = _insights_subtabs("recaps")
    recap = []
    for wk, ms in reversed(await _played_weeks()):  # newest week first
        ann = results.announcement(views.scoreboard(ms, rosters, users), wk)
        if ann:
            recap.append(ann)
    ctx["recaps"] = recap
    ctx["has_games"] = bool(recap)
    return templates.TemplateResponse(request, "recaps.html", ctx)


@app.get("/rivalry", response_class=HTMLResponse)
async def rivalry(request: Request, a: int | None = None, b: int | None = None):
    if not cfg.enable_analysis:
        return RedirectResponse("/")
    ctx = await _base_ctx(request, "insights")
    ctx["subtabs"] = _insights_subtabs("rivalry")
    users, rosters = await get_users(lid()), await get_rosters(lid())
    owned = [r for r in rosters if r.get("owner_id")]
    by_uid = {u["user_id"]: u for u in users}
    picker = sorted(({"roster_id": r["roster_id"], "name": views.team_name(by_uid.get(r["owner_id"]))}
                     for r in owned), key=lambda x: x["name"].lower())
    ctx["teams"] = picker
    if len(picker) >= 2:
        ids = {p["roster_id"] for p in picker}
        a = a if a in ids else (ctx.get("me_roster_id") if ctx.get("me_roster_id") in ids else picker[0]["roster_id"])
        b = b if b in ids else next((p["roster_id"] for p in picker if p["roster_id"] != a), a)
        ctx["a_id"], ctx["b_id"] = a, b
        ctx["rivalry"] = views.rivalry(await _played_weeks(), a, b, users, rosters)
    return templates.TemplateResponse(request, "rivalry.html", ctx)


@app.get("/rules", response_class=HTMLResponse)
async def rules(request: Request):
    ctx = await _base_ctx(request, "home")
    ctx["rules"] = views.league_rules(await get_league(lid()))
    return templates.TemplateResponse(request, "rules.html", ctx)


@app.get("/playoffs", response_class=HTMLResponse)
async def playoffs(request: Request):
    if not cfg.enable_analysis:
        return RedirectResponse("/")
    ctx = await _base_ctx(request, "insights")
    users, rosters, league = await get_users(lid()), await get_rosters(lid()), await get_league(lid())
    standings = views.standings(users, rosters)
    scores, remaining = await _season_frame()
    n = (league.get("settings") or {}).get("playoff_teams", 6)
    ctx["subtabs"] = _insights_subtabs("playoffs")
    ctx["playoff_teams"] = n
    ctx["odds"] = views.playoff_odds(scores, standings, remaining, n) if standings else []
    ctx["has_games"] = bool(ctx["odds"])
    ctx["remaining_note"] = f"{len(remaining)} matchups simulated · top {n} make the playoffs"
    return templates.TemplateResponse(request, "playoffs.html", ctx)


@app.get("/bets", response_class=HTMLResponse)
async def bets(request: Request):
    if not cfg.enable_betting:
        return RedirectResponse("/")
    ctx = await _base_ctx(request, "gamble")
    ctx["subtabs"] = _gamble_subtabs("bets")
    users, rosters = await get_users(lid()), await get_rosters(lid())
    by_id = {u["user_id"]: u for u in users}
    owner_of = {r["roster_id"]: by_id.get(r.get("owner_id")) for r in rosters if r.get("owner_id")}
    state = await get_nfl_state()
    current = state.get("week") or 1

    # Settled results across every played week (matchup_result is None until a game is decided).
    results, cur_week = {}, []
    for wk in range(1, current + 1):
        m = await get_matchups(lid(), wk)
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
    if betting.matchup_result(await get_matchups(lid(), current), me) is not None:
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
    users, rosters = await get_users(lid()), await get_rosters(lid())
    by_id = {u["user_id"]: u for u in users}
    owner_of = {r["roster_id"]: by_id.get(r.get("owner_id")) for r in rosters if r.get("owner_id")}

    board = h2h.games(await odds.get_nfl_odds())
    # team logos + match each game to its ESPN event id (by team name) so a card links to /game
    current = (await get_nfl_state()).get("week") or 1
    try:
        espn_games = espn.games(await espn.scoreboard(year=int(await _season()), week=current))["games"]
    except Exception:
        espn_games = []
    eid_by_teams = {(g["home"]["name"], g["away"]["name"]): g["id"] for g in espn_games}
    for g in board:
        g["home_logo"] = views.team_logo_by_name(g["home"])
        g["away_logo"] = views.team_logo_by_name(g["away"])
        g["eid"] = eid_by_teams.get((g["home"], g["away"]))
        g["week"] = current
    ctx["games"] = board

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


_emoji_markup: dict[str, str] = {}  # emoji name -> "<:name:id>", fetched once per process


async def _guild_emojis() -> dict:
    """Custom-emoji markup for the bet channel's server, keyed by name. Cached; best-effort."""
    if _emoji_markup or not cfg.bet_channel_id:
        return _emoji_markup
    headers = {"Authorization": f"Bot {cfg.discord_token}"}
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            gid = (await c.get(f"https://discord.com/api/v10/channels/{cfg.bet_channel_id}",
                               headers=headers)).json().get("guild_id")
            if not gid:
                return _emoji_markup
            emojis = (await c.get(f"https://discord.com/api/v10/guilds/{gid}/emojis",
                                  headers=headers)).json()
        for e in emojis:
            if e.get("name") and e.get("id"):
                _emoji_markup[e["name"]] = f"<:{e['name']}:{e['id']}>"
    except (httpx.HTTPError, ValueError, KeyError):
        pass
    return _emoji_markup


async def _post_bet_to_discord(game: dict, match: dict, pair: dict | None, stake: int,
                               proposer_discord_id: str | None, wager_id: int) -> None:
    """Post a proposed bet to the bet channel: pings the proposer, uses team emojis, and leads
    with the pick so the side is obvious. Best-effort — a Discord failure never breaks propose."""
    if not cfg.bet_channel_id:
        return
    taker_stake = h2h.american_profit(stake, match["price"])  # taker risks the proposer's profit
    em = await _guild_emojis()
    def emoji(team):  # team full name -> "<:nick:id> " or ""
        m = em.get(views.team_nick(team) or "")
        return f"{m} " if m else ""
    pick_team = next((t for t in (game["home"], game["away"]) if match["side"].startswith(t)), None)
    desc = [f"**Taking {emoji(pick_team)}{match['label']}  ({match['price']:+d})**",
            f"Risk **{stake}** to win **{taker_stake}**  ·  {match['market']}"]
    if pair:
        desc.append(f"\nClaim the other side — **{pair['label']}** ({pair['price']:+d}): "
                    f"risk **{taker_stake}** to win **{stake}**")
    embed = {
        "title": f"{emoji(game['away'])}{game['away']}   @   {emoji(game['home'])}{game['home']}",
        "description": "\n".join(desc),
        "color": 0xC9A05E,
        "footer": {"text": "No-vig line · play money"},
    }
    who = f"<@{proposer_discord_id}>" if proposer_discord_id else "Someone"
    payload = {
        "content": f"{who} is looking for action",
        "embeds": [embed],
        "components": [{"type": 1, "components": [
            {"type": 2, "style": 3, "label": (f"Claim {pair['label']}" if pair else "Claim it")[:80],
             "custom_id": f"claim:{wager_id}"}]}],
    }
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            await c.post(f"https://discord.com/api/v10/channels/{cfg.bet_channel_id}/messages",
                         headers={"Authorization": f"Bot {cfg.discord_token}"}, json=payload)
    except httpx.HTTPError:
        pass


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
    if stake not in (10, 25, 50, 100):  # only the offered sizes
        return RedirectResponse("/h2h", 303)
    board = h2h.games(await odds.get_nfl_odds())
    game = next((g for g in board if g["game_id"] == game_id), None)
    if game is None:
        return RedirectResponse("/h2h", 303)
    match = next((s for s in game["selections"] if s["side"] == side and s["price"] == price), None)
    if match is None:
        return RedirectResponse("/h2h", 303)  # not a current selection — reject cleanly
    pair = next((s for s in game["selections"]
                 if s["market"] == match["market"] and s["side"] != side), None)
    try:
        wid = h2h.propose(me, game_id, side, price, stake)
    except ValueError:
        return RedirectResponse("/h2h", 303)  # invalid stake
    await _post_bet_to_discord(game, match, pair, stake, request.session.get("discord_id"), wid)
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
