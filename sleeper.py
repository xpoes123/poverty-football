"""Sleeper read layer. Read-only, no auth. Shared by the bot and the website.

A tiny in-process TTL cache keeps us well under Sleeper's rate limit and makes the
site snappy. No lock — a rare double-fetch on expiry is fine (ponytail: add a lock
only if request volume ever makes the race matter, which for a 12-person league it won't).
"""

import contextvars
import time

import httpx

from config import cfg

BASE = "https://api.sleeper.app/v1"

# The league the current web request is viewing (set by web.py middleware from the
# `league` cookie). Defaults to the bot's league so the bot and any non-request code
# just work. web.py reads it via lid().
active_league: contextvars.ContextVar[str] = contextvars.ContextVar("active_league", default=cfg.league_id)

_cache: dict[str, tuple[float, object]] = {}


async def _get(url: str, ttl: float):
    now = time.monotonic()
    hit = _cache.get(url)
    if hit and hit[0] > now:
        return hit[1]
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get(url)
            r.raise_for_status()
            data = r.json()
    except (httpx.HTTPError, ValueError):
        if hit is not None:
            return hit[1]  # serve stale rather than 500 the page on a transient upstream blip
        raise
    _cache[url] = (now + ttl, data)
    return data


# --- league ---------------------------------------------------------------
async def get_league(league_id: str) -> dict:
    return await _get(f"{BASE}/league/{league_id}", ttl=300)


async def get_league_meta(league_id: str) -> tuple[str, int, str]:
    d = await get_league(league_id)
    return d["name"], d["total_rosters"], d["status"]


async def get_users(league_id: str) -> list[dict]:
    return await _get(f"{BASE}/league/{league_id}/users", ttl=60)


async def get_joined_user_ids(league_id: str) -> set[str]:
    return {u["user_id"] for u in await get_users(league_id)}


async def get_rosters(league_id: str) -> list[dict]:
    return await _get(f"{BASE}/league/{league_id}/rosters", ttl=30)


async def get_claimed_team_count(league_id: str) -> int:
    return sum(1 for r in await get_rosters(league_id) if r.get("owner_id"))


async def get_matchups(league_id: str, week: int) -> list[dict]:
    return await _get(f"{BASE}/league/{league_id}/matchups/{week}", ttl=30)


async def get_drafts(league_id: str) -> list[dict]:
    return await _get(f"{BASE}/league/{league_id}/drafts", ttl=300)


async def get_draft_picks(draft_id: str) -> list[dict]:
    return await _get(f"{BASE}/draft/{draft_id}/picks", ttl=300)


async def get_transactions(league_id: str, week: int) -> list[dict]:
    return await _get(f"{BASE}/league/{league_id}/transactions/{week}", ttl=60)


# --- global ---------------------------------------------------------------
async def resolve_user_id(username: str) -> str | None:
    data = await _get(f"{BASE}/user/{username}", ttl=600)  # None for unknown users
    return data["user_id"] if data else None


async def get_nfl_state() -> dict:
    return await _get(f"{BASE}/state/nfl", ttl=300)


async def get_trending_adds(limit: int = 25) -> list[dict]:
    """Most-added players league-wide (Sleeper trending) -> [{player_id, count}]. Empty on error."""
    try:
        return await _get(f"{BASE}/players/nfl/trending/add?limit={limit}", ttl=1800) or []
    except httpx.HTTPStatusError:
        return []


_PROJ = "https://api.sleeper.app/projections/nfl"
_POS_Q = "".join(f"&position[]={p}" for p in ("QB", "RB", "WR", "TE", "K", "DEF"))


async def get_projections(season: str, week: int) -> dict:
    """Projected stats per player for a week -> {player_id: stats}. Always real NFL data
    (not seed-gated) — keyed by real player_id, which the seed rosters use too."""
    url = f"{_PROJ}/{season}/{week}?season_type=regular{_POS_Q}"
    now = time.monotonic()
    hit = _cache.get(url)
    if hit and hit[0] > now:
        return hit[1]
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get(url)
            r.raise_for_status()
            rows = r.json()
    except Exception:
        rows = []
    out = {str(x["player_id"]): (x.get("stats") or {}) for x in rows if x.get("player_id")}
    _cache[url] = (now + 1800, out)
    return out


async def get_players() -> dict:
    """id -> player metadata. ~5MB, so cache hard (24h) and only fetch when a page needs names."""
    return await _get(f"{BASE}/players/nfl", ttl=86400)


async def get_player_stats(season: str) -> dict:
    """id -> season stat totals (pts_ppr, etc.). Empty dict if the season has no data."""
    try:
        return await _get(f"{BASE}/stats/nfl/regular/{season}", ttl=86400) or {}
    except httpx.HTTPStatusError:
        return {}


async def get_week_stats(season: str, week: int) -> dict:
    """id -> that week's stat line. Empty dict if unavailable."""
    try:
        return await _get(f"{BASE}/stats/nfl/regular/{season}/{week}", ttl=3600) or {}
    except httpx.HTTPStatusError:
        return {}
