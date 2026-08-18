"""Sleeper read layer. Read-only, no auth. Grows into the shared client later."""

import httpx

BASE = "https://api.sleeper.app/v1"


async def get_joined_user_ids(league_id: str) -> set[str]:
    """user_ids of everyone who has actually joined the league."""
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(f"{BASE}/league/{league_id}/users")
        r.raise_for_status()
        return {u["user_id"] for u in r.json()}


async def get_claimed_team_count(league_id: str) -> int:
    """Rosters with an owner — counts co-owned teams once (a team, not per account)."""
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(f"{BASE}/league/{league_id}/rosters")
        r.raise_for_status()
        return sum(1 for roster in r.json() if roster.get("owner_id"))


async def get_league_meta(league_id: str) -> tuple[str, int, str]:
    """(league name, total roster slots, status) — for the embed header."""
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(f"{BASE}/league/{league_id}")
        r.raise_for_status()
        d = r.json()
        return d["name"], d["total_rosters"], d["status"]


async def resolve_user_id(username: str) -> str | None:
    """Sleeper username -> user_id, or None if no such user (typo / not signed up)."""
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(f"{BASE}/user/{username}")
        r.raise_for_status()
        data = r.json()  # Sleeper returns literal null for unknown users
        return data["user_id"] if data else None
