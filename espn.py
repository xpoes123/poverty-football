"""ESPN NFL read layer (free public JSON API, no key) for the real-game pages.
Own small TTL cache — independent of the Sleeper layer and the dev-seed switch."""

import time

import httpx

BASE = "https://site.api.espn.com/apis/site/v2/sports/football/nfl"
_cache: dict[str, tuple[float, object]] = {}


async def _get(url: str, ttl: float):
    now = time.monotonic()
    hit = _cache.get(url)
    if hit and hit[0] > now:
        return hit[1]
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(url)
        r.raise_for_status()
        data = r.json()
    _cache[url] = (now + ttl, data)
    return data


async def scoreboard(year: int = 2025, week: int = 1, seasontype: int = 2) -> dict:
    # historical (final) weeks — cache long
    return await _get(f"{BASE}/scoreboard?dates={year}&seasontype={seasontype}&week={week}", ttl=1800)


async def summary(event_id: str) -> dict:
    return await _get(f"{BASE}/summary?event={event_id}", ttl=1800)  # historical games are final


SLEEPER_TO_ESPN_TEAM = {"WAS": "WSH"}


async def team_roster(team_abbr: str) -> dict:
    return await _get(f"{BASE}/teams/{team_abbr}/roster", ttl=86400)


def _norm_name(s: str) -> str:
    return "".join(c for c in (s or "").lower() if c.isalnum())


async def resolve_athlete(name: str, team_abbr: str) -> str | None:
    """Find a player's ESPN athlete id via their team roster (for players Sleeper has no espn_id for)."""
    if not team_abbr:
        return None
    try:
        r = await team_roster(SLEEPER_TO_ESPN_TEAM.get(team_abbr, team_abbr))
    except Exception:
        return None
    target = _norm_name(name)
    for group in r.get("athletes", []):
        for a in group.get("items", []):
            if _norm_name(a.get("fullName")) == target:
                return a.get("id")
    return None


async def gamelog(espn_id: str, season: int = 2025) -> dict:
    url = f"https://site.web.api.espn.com/apis/common/v3/sports/football/nfl/athletes/{espn_id}/gamelog?season={season}"
    return await _get(url, ttl=3600)


# ESPN gamelog stat name → (short label, our Sleeper scoring key)
_LOG_STATS = {
    "passingYards": ("Pass Yd", "pass_yd"), "passingTouchdowns": ("Pass TD", "pass_td"),
    "interceptions": ("INT", "pass_int"), "rushingAttempts": ("Car", "rush_att"),
    "rushingYards": ("Rush Yd", "rush_yd"), "rushingTouchdowns": ("Rush TD", "rush_td"),
    "receptions": ("Rec", "rec"), "receivingTargets": ("Tgt", "rec_tgt"),
    "receivingYards": ("Rec Yd", "rec_yd"), "receivingTouchdowns": ("Rec TD", "rec_td"),
}


def _fnum(v):
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


ESPN_POSCOLS = {
    "QB": [("Pass Yd", "passingYards"), ("Pass TD", "passingTouchdowns"), ("INT", "interceptions"),
           ("Rush Yd", "rushingYards"), ("Rush TD", "rushingTouchdowns")],
    "RB": [("Car", "rushingAttempts"), ("Rush Yd", "rushingYards"), ("Rush TD", "rushingTouchdowns"),
           ("Rec", "receptions"), ("Rec Yd", "receivingYards"), ("Rec TD", "receivingTouchdowns")],
    "WR": [("Tgt", "receivingTargets"), ("Rec", "receptions"), ("Rec Yd", "receivingYards"),
           ("Rec TD", "receivingTouchdowns"), ("Rush Yd", "rushingYards")],
    "TE": [("Tgt", "receivingTargets"), ("Rec", "receptions"), ("Rec Yd", "receivingYards"),
           ("Rec TD", "receivingTouchdowns")],
}


def game_table(gl: dict, scoring: dict, position: str) -> dict:
    """Full-season game history as a sortable table with position-specific stat columns."""
    cols = ESPN_POSCOLS.get(position, [])
    names = gl.get("names") or []
    events = gl.get("events") or {}
    cats = (gl.get("seasonTypes") or [{}])[0].get("categories") or []
    src = cats[0].get("events") if cats else []
    rows = []
    for ev in src:
        eid = ev.get("eventId")
        d = dict(zip(names, ev.get("stats") or []))
        meta = events.get(eid, {})
        cells = []
        for _, key in cols:
            v = d.get(key)
            n = _fnum(v)
            disp = (int(n) if n == int(n) else round(n, 1)) if v not in (None, "", "--", "-") else "—"
            cells.append({"v": disp, "n": n})
        pts = round(sum(_fnum(d.get(nm)) * scoring.get(k, 0) for nm, (_, k) in _LOG_STATS.items() if nm in d), 2)
        gr = (meta.get("gameResult") or "").strip()
        sc = (meta.get("score") or "").strip()
        rows.append({"week": meta.get("week"), "opp": (meta.get("opponent") or {}).get("abbreviation"),
                     "atvs": meta.get("atVs"), "eid": eid, "cells": cells, "pts": pts,
                     "result": gr if any(ch.isdigit() for ch in gr) else f"{gr} {sc}".strip()})
    rows.sort(key=lambda r: r["week"] or 0, reverse=True)  # most recent first
    return {"columns": [label for label, _ in cols], "rows": rows}


# --- pure shaping (testable on raw JSON) ------------------------------------
def _side(comp: dict) -> dict:
    t = comp.get("team", {})
    return {"name": t.get("displayName"), "abbr": t.get("abbreviation"),
            "logo": t.get("logo"), "score": comp.get("score"), "winner": comp.get("winner")}


def games(sb: dict) -> dict:
    out = []
    for e in sb.get("events", []):
        c = (e.get("competitions") or [{}])[0]
        comps = c.get("competitors") or []
        st = c.get("status", {}).get("type", {})
        out.append({
            "id": e.get("id"), "short": e.get("shortName"),
            "state": st.get("state"), "status": st.get("shortDetail") or st.get("description"),
            "date": e.get("date"),
            "home": _side(next((x for x in comps if x.get("homeAway") == "home"), {})),
            "away": _side(next((x for x in comps if x.get("homeAway") == "away"), {})),
        })
    wk = (sb.get("week") or {}).get("number")
    return {"games": out, "week": wk}


def week_schedule(sb: dict) -> dict:
    """ESPN team abbr -> {opp, at ('@'|'vs'), kick (iso)} for each NFL game that week."""
    out = {}
    for g in games(sb)["games"]:
        h, a, kick = g["home"]["abbr"], g["away"]["abbr"], g["date"]
        if h and a:
            out[h] = {"opp": a, "at": "vs", "kick": kick}
            out[a] = {"opp": h, "at": "@", "kick": kick}
    return out


def detail(s: dict) -> dict | None:
    comp = ((s.get("header") or {}).get("competitions") or [{}])[0]
    comps = comp.get("competitors") or []
    if not comps:
        return None
    ha = {x.get("team", {}).get("id"): x.get("homeAway") for x in comps}

    def header_side(which):
        x = next((c for c in comps if c.get("homeAway") == which), {})
        t = x.get("team", {})
        logo = t.get("logo") or ((t.get("logos") or [{}])[0]).get("href")
        return {"name": t.get("displayName"), "abbr": t.get("abbreviation"), "logo": logo,
                "score": x.get("score"), "winner": x.get("winner")}

    # team stats comparison, aligned away | label | home
    rows = []
    tstats = {ha.get(t.get("team", {}).get("id")): t for t in (s.get("boxscore") or {}).get("teams", [])}
    ta, th = tstats.get("away"), tstats.get("home")
    if ta and th:
        sh = {st.get("label"): st.get("displayValue") for st in th.get("statistics", [])}
        for st in ta.get("statistics", []):
            lbl = st.get("label")
            rows.append({"label": lbl, "away": st.get("displayValue"), "home": sh.get(lbl, "—")})

    # leaders: passing / rushing / receiving per side
    cats = {"passingYards": "Passing", "rushingYards": "Rushing", "receivingYards": "Receiving"}
    leaders = {"away": [], "home": []}
    for grp in s.get("leaders", []):
        side = ha.get(grp.get("team", {}).get("id"))
        if side not in leaders:
            continue
        for cat in grp.get("leaders", []):
            if cat.get("name") in cats and cat.get("leaders"):
                ath = cat["leaders"][0]
                athlete = ath.get("athlete", {})
                href = next((l.get("href") for l in (athlete.get("links") or [])
                             if "playercard" in (l.get("rel") or [])), None)
                leaders[side].append({"cat": cats[cat["name"]], "name": athlete.get("displayName"),
                                      "stat": ath.get("displayValue"),
                                      "img": (athlete.get("headshot") or {}).get("href"), "href": href})

    return {
        "away": header_side("away"), "home": header_side("home"),
        "status": comp.get("status", {}).get("type", {}).get("shortDetail"),
        "date": comp.get("date"),
        "stat_rows": rows, "leaders": leaders,
    }
