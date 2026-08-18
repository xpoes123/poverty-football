"""Sleeper read layer. Read-only, no auth. Shared by the bot and the website.

A tiny in-process TTL cache keeps us well under Sleeper's rate limit and makes the
site snappy. No lock — a rare double-fetch on expiry is fine (ponytail: add a lock
only if request volume ever makes the race matter, which for a 12-person league it won't).
"""

import contextvars
import json
import pathlib
import time

import httpx

from config import cfg

BASE = "https://api.sleeper.app/v1"
SEED_DIR = pathlib.Path(__file__).parent / "seed"

# Per-request override so a visitor can preview seeded season data via a cookie,
# independent of the global cfg.dev_seed env flag. Set by web.py middleware.
seed_preview: contextvars.ContextVar[bool] = contextvars.ContextVar("seed_preview", default=False)

_cache: dict[str, tuple[float, object]] = {}


def _seed(url: str):
    """Map a Sleeper URL to a local fixture (dev_seed mode). Missing files fall
    back to a shape-correct empty value so absent weeks/users never crash."""
    parts = url[len(BASE):].strip("/").split("/")

    def load(fname: str, fallback):
        p = SEED_DIR / fname
        return json.loads(p.read_text()) if p.exists() else fallback

    if parts[0] == "league":
        if len(parts) == 2:
            return load("league.json", {})
        sub = parts[2]
        if sub == "users":
            return load("users.json", [])
        if sub == "rosters":
            return load("rosters.json", [])
        if sub == "matchups":
            return load(f"matchups_{parts[3]}.json", [])
        if sub == "transactions":
            return load(f"transactions_{parts[3]}.json", [])
        if sub == "drafts":
            return load("drafts.json", [])
    elif parts[0] == "draft":  # draft/{id}/picks
        return load("draft_picks.json", [])
    elif parts[0] == "state":
        return load("nfl_state.json", {})
    elif parts[0] == "players":
        return load("players.json", {})
    elif parts[0] == "stats":  # stats/nfl/regular/{season}[/{week}]
        return load("week_stats.json", {}) if len(parts) >= 5 else load(f"stats_{parts[3]}.json", {})
    elif parts[0] == "user":  # resolve handle -> user dict, or None
        name = parts[1]
        return next((u for u in load("users.json", []) if u.get("display_name") == name), None)
    return None


async def _get(url: str, ttl: float):
    if cfg.dev_seed or seed_preview.get():
        return _seed(url)
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
