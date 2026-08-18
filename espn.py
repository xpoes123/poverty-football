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
    return await _get(f"{BASE}/summary?event={event_id}", ttl=180)


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
        return {"name": t.get("displayName"), "logo": logo,
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
                leaders[side].append({"cat": cats[cat["name"]],
                                      "name": ath.get("athlete", {}).get("displayName"),
                                      "stat": ath.get("displayValue")})

    return {
        "away": header_side("away"), "home": header_side("home"),
        "status": comp.get("status", {}).get("type", {}).get("shortDetail"),
        "stat_rows": rows, "leaders": leaders,
    }
