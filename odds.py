"""NFL game odds read layer (flag: cfg.enable_h2h_betting).

Mirrors sleeper.py: a tiny in-process TTL cache keeps us well under the-odds-api's
quota, and cfg.dev_seed swaps the network for a local fixture so the in-season UI can
be built off-season. A missing/empty key degrades to an empty board — never a crash.
Never call at import time; only from the route.
"""

import json
import pathlib
import time

import httpx

from config import cfg

BASE = "https://api.the-odds-api.com/v4"
SEED_DIR = pathlib.Path(__file__).parent / "seed"
ODDS_URL = f"{BASE}/sports/americanfootball_nfl/odds"

_cache: dict[str, tuple[float, object]] = {}


async def get_nfl_odds() -> list[dict]:
    """This week's NFL games with h2h (moneyline) odds, the-odds-api v4 shape.

    dev_seed → local fixture; no key → empty board; else a cached live fetch. Any
    HTTP error degrades to an empty list."""
    if cfg.dev_seed:
        p = SEED_DIR / "nfl_odds.json"
        return json.loads(p.read_text()) if p.exists() else []
    if not cfg.odds_api_key:
        return []
    now = time.monotonic()
    hit = _cache.get(ODDS_URL)
    if hit and hit[0] > now:
        return hit[1]
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get(ODDS_URL, params={
                "apiKey": cfg.odds_api_key, "regions": "us",
                "markets": "h2h,spreads,totals", "oddsFormat": "american"})
            r.raise_for_status()
            data = r.json()
    except (httpx.HTTPStatusError, httpx.HTTPError):
        return []
    _cache[ODDS_URL] = (now + 3600, data)  # 1h TTL — do not hammer the quota
    return data
