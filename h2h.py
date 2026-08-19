"""Head-to-head NFL-game betting (flag: cfg.enable_h2h_betting).

Members bet against each other on that week's real NFL games. One member proposes a
side at a price; another shakes hands on the other side. When the game is final the
proposer's payout (american odds on the stake) is credited and the acceptor is
debited the mirror. Play-money — no real stakes.

Split like betting.py: the top half is PURE (plain dicts/lists, unit-testable, no
I/O), the bottom is a thin sqlite3 store.
"""

import os
import pathlib
import sqlite3
import time

# --- pure logic -----------------------------------------------------------


def _implied_prob(american: int) -> float:
    """American odds -> implied win probability (includes the book's vig)."""
    return 100 / (american + 100) if american > 0 else -american / (-american + 100)


def _fair_american(p: float) -> int:
    """Probability -> fair american odds (+underdog / -favorite; pick'em = +100)."""
    if p <= 0 or p >= 1:
        return 0
    return round(100 * (1 - p) / p) if p <= 0.5 else -round(100 * p / (1 - p))


def devig_two_way(home_price: int, away_price: int) -> tuple[int, int]:
    """Strip the vig from a two-way moneyline: normalize the implied probabilities so
    they sum to 1, then convert back to fair american odds."""
    ph, pa = _implied_prob(home_price), _implied_prob(away_price)
    total = ph + pa
    if total <= 0:
        return home_price, away_price
    return _fair_american(ph / total), _fair_american(pa / total)


def games(odds: list[dict]) -> list[dict]:
    """Flatten the-odds-api v4 objects to one row per game using the FIRST bookmaker
    that carries an h2h market. Moneylines are devigged to fair no-vig odds (play-money
    h2h, no house edge). Games with no h2h anywhere are skipped."""
    out = []
    for o in odds:
        home, away = o.get("home_team"), o.get("away_team")
        market = None
        for bk in o.get("bookmakers") or []:
            market = next((m for m in bk.get("markets") or [] if m.get("key") == "h2h"), None)
            if market:
                break
        if not market:
            continue
        prices = {oc.get("name"): oc.get("price") for oc in market.get("outcomes") or []}
        home_price, away_price = prices.get(home), prices.get(away)
        if home_price is None or away_price is None:
            continue
        home_price, away_price = devig_two_way(home_price, away_price)
        favorite = home if home_price <= away_price else away  # more-negative price = favorite
        out.append({"game_id": o.get("id"), "home": home, "away": away,
                    "commence": o.get("commence_time"),
                    "home_price": home_price, "away_price": away_price, "favorite": favorite})
    return out


def american_profit(stake: int, price: int) -> int:
    """Profit (not incl. stake) on a winning bet at american odds."""
    if price > 0:
        return round(stake * price / 100)
    return round(stake * 100 / abs(price))


def settle(wagers: list[dict], results: dict) -> list[dict]:
    """Attach status/payout to each wager. results maps game_id -> winning team name
    (absent = not final). No acceptor -> 'open' (0); accepted but game unresolved ->
    'matched' (0); final & proposer's side won -> +profit 'won'; lost -> -stake 'lost'.
    Payout is always from the PROPOSER's perspective."""
    out = []
    for w in wagers:
        stake, price = w["stake"], w["price"]
        if w.get("acceptor") is None:
            payout, status = 0, "open"
        else:
            winner = results.get(w["game_id"])
            if winner is None:
                payout, status = 0, "matched"
            elif winner == w["side"]:
                payout, status = american_profit(stake, price), "won"
            else:
                payout, status = -stake, "lost"
        out.append({**w, "payout": payout, "status": status})
    return out


def net_ledger(settled: list[dict]) -> dict:
    """roster_id -> net chips. Each decided wager credits the proposer its payout and
    debits the acceptor the mirror. Open/matched contribute nothing."""
    net: dict = {}
    for w in settled:
        if w["status"] not in ("won", "lost"):
            continue
        payout = w["payout"]
        net[w["proposer"]] = net.get(w["proposer"], 0) + payout
        net[w["acceptor"]] = net.get(w["acceptor"], 0) - payout
    return net


# --- thin sqlite store -----------------------------------------------------
DB_PATH = pathlib.Path(__file__).parent / "data" / "h2h.db"


def _resolve(path=None) -> pathlib.Path:
    return pathlib.Path(path or os.environ.get("H2H_DB") or DB_PATH)


def _conn(path=None) -> sqlite3.Connection:
    p = _resolve(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p)
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE IF NOT EXISTS wagers ("
        "id INTEGER PRIMARY KEY, game_id TEXT, side TEXT, price INT, stake INT, "
        "proposer INT, acceptor INT, created REAL)"
    )
    return conn


def propose(proposer: int, game_id: str, side: str, price: int, stake: int, path=None) -> None:
    """Record an open handshake: proposer stakes `side` at `price`, acceptor NULL.
    Rejects a non-positive/non-int stake (same guard as betting.place_bet)."""
    if isinstance(stake, bool) or not isinstance(stake, int) or stake <= 0:
        raise ValueError("stake must be a positive integer")
    with _conn(path) as conn:
        conn.execute(
            "INSERT INTO wagers (game_id, side, price, stake, proposer, acceptor, created) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (str(game_id), str(side), int(price), stake, int(proposer), None, time.time()),
        )


def accept(wager_id: int, acceptor: int, path=None) -> None:
    """Shake on the other side of an open wager. Raises ValueError if it's already
    taken, missing, or the acceptor is the proposer (no self-accept)."""
    with _conn(path) as conn:
        cur = conn.execute(
            "UPDATE wagers SET acceptor=? WHERE id=? AND acceptor IS NULL AND proposer != ?",
            (int(acceptor), int(wager_id), int(acceptor)),
        )
        if cur.rowcount == 0:
            raise ValueError("wager already taken, missing, or self-accept")


def _rows(where: str, path=None) -> list[dict]:
    with _conn(path) as conn:
        rows = conn.execute(
            "SELECT id, game_id, side, price, stake, proposer, acceptor, created "
            f"FROM wagers {where} ORDER BY created DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def open_wagers(path=None) -> list[dict]:
    return _rows("WHERE acceptor IS NULL", path)


def matched_wagers(path=None) -> list[dict]:
    return _rows("WHERE acceptor IS NOT NULL", path)


def all_wagers(path=None) -> list[dict]:
    return _rows("", path)
