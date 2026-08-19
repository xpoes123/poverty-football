"""NFL game odds read layer (flag: cfg.enable_h2h_betting).

Odds are recycled from SharpLab's pipeline (it already ingests the-odds-api with central
quota management) via its slate endpoint — we don't hit the-odds-api ourselves. cfg.dev_seed
swaps the network for a local fixture so the in-season UI can be built off-season. Any error
degrades to the last good board (or empty) — never a crash. Call only from the route.
"""

import json
import pathlib
import time

import httpx

from config import cfg

SEED_DIR = pathlib.Path(__file__).parent / "seed"
_cache: dict[str, tuple[float, object]] = {}
_BOOK_PREF = ("draftkings", "fanduel", "betmgm", "caesars")  # which book's line to show


def _to_oddsapi_shape(slate_games: list[dict]) -> list[dict]:
    """SharpLab slate games -> the-odds-api v4 game shape, so h2h.games() parses them
    unchanged. Picks one bookmaker's line per game (prefers DK)."""
    out = []
    for g in slate_games:
        books = g.get("odds") or {}
        src = next((b for b in _BOOK_PREF if b in books), next(iter(books), None))
        if src is None:
            continue
        p = books[src]
        markets = []
        if p.get("ml_home") is not None and p.get("ml_away") is not None:
            markets.append({"key": "h2h", "outcomes": [
                {"name": g["home_team"], "price": p["ml_home"]},
                {"name": g["away_team"], "price": p["ml_away"]}]})
        if p.get("spread") is not None and p.get("spread_odds") is not None \
                and p.get("spread_away") is not None and p.get("spread_away_odds") is not None:
            markets.append({"key": "spreads", "outcomes": [
                {"name": g["home_team"], "price": p["spread_odds"], "point": p["spread"]},
                {"name": g["away_team"], "price": p["spread_away_odds"], "point": p["spread_away"]}]})
        if p.get("total") is not None and p.get("total_over_odds") is not None \
                and p.get("total_under_odds") is not None:
            markets.append({"key": "totals", "outcomes": [
                {"name": "Over", "price": p["total_over_odds"], "point": p["total"]},
                {"name": "Under", "price": p["total_under_odds"], "point": p["total"]}]})
        if not markets:
            continue
        out.append({"id": g["game_id"], "commence_time": g.get("start_time"),
                    "home_team": g["home_team"], "away_team": g["away_team"],
                    "bookmakers": [{"key": src, "title": src, "markets": markets}]})
    return out


async def get_nfl_odds() -> list[dict]:
    """This week's NFL games with odds, the-odds-api v4 shape (h2h.games() consumes it).

    dev_seed → local fixture; else SharpLab's slate feed, cached. Errors degrade to the
    last good board (or empty)."""
    if cfg.dev_seed:
        p = SEED_DIR / "nfl_odds.json"
        return json.loads(p.read_text()) if p.exists() else []
    url = cfg.sharplab_slate_url
    now = time.monotonic()
    hit = _cache.get(url)
    if hit and hit[0] > now:
        return hit[1]
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(url, params={"sport": "nfl"})
            r.raise_for_status()
            slate = r.json().get("games", [])
    except (httpx.HTTPError, ValueError):
        return _cache.get(url, (0, []))[1]  # last good board, or empty
    data = _to_oddsapi_shape(slate)
    _cache[url] = (now + 600, data)  # 10 min — SharpLab already rate-limits the upstream fetch
    return data
