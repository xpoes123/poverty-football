"""Generate internally-consistent, Sleeper-shaped DEV fixtures.

Run once and commit the JSON output alongside this script:

    ./.venv/bin/python seed/build_fixtures.py

Everything emitted here is OBVIOUSLY-SYNTHETIC dev data — franchise names,
player names, teams, scores and stat lines are procedurally generated from
fixed pools, never real NFL facts or results. These fixtures are served ONLY
when ``cfg.dev_seed`` is True (so the in-season UI can be built off-season)
and are never shown on the live site. The generator is deterministic (seeded
RNG) so re-running reproduces byte-for-byte the same league.

Shapes mirror the real Sleeper payloads that views.py already parses:
league / users / rosters / matchups_{w} / transactions_{w} / players /
stats_{season} / nfl_state. Player ids line up across rosters, stats,
matchups and transactions because a single pass generates them together.
"""

import datetime as dt
import json
import pathlib
import random

SEED_DIR = pathlib.Path(__file__).parent
RNG = random.Random(20250817)

SEASON = "2025"
CURRENT_WEEK = 6
LEAGUE_ID = "dev000000000000000"

ROSTER_POSITIONS = ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "K", "DEF",
                    "BN", "BN", "BN", "BN", "BN"]

NFL_TEAMS = ["ARI", "ATL", "BAL", "BUF", "CAR", "CHI", "CIN", "CLE", "DAL",
             "DEN", "DET", "GB", "HOU", "IND", "JAX", "KC", "LAC", "LAR",
             "LV", "MIA", "MIN", "NE", "NO", "NYG", "NYJ", "PHI", "PIT",
             "SEA", "SF", "TB", "TEN", "WAS"]

# 12 owner handles + franchise names — deliberately playful & clearly synthetic.
HANDLES = ["retraso", "xpoes", "boomstick", "coldbrew", "third_down", "punter",
           "gridlock", "hail_mary", "audible", "red_zone", "two_minute", "onside"]
FRANCHISES = ["Espresso Enforcers", "Antique Gold Rush", "Ledger Legends",
              "Almanac All-Stars", "Fraunces Faithful", "Narrow Margins",
              "Bench Warmers Local 12", "The Coffee Grinders", "Pigskin Paupers",
              "Sunday Scaries FC", "The Bye Weekenders", "Fourth & Broke"]

# Synthetic name pools. Combinations are procedural; any resemblance to a real
# player is coincidental and, being dev-only, immaterial.
FIRST = ["Deshawn", "Kylen", "Marquis", "Tobias", "Rafferty", "Emeka", "Grady",
         "Ozzie", "Salvatore", "Bexley", "Corwin", "Dashiell", "Ellery",
         "Fitzgerald", "Huxley", "Idris", "Jarett", "Kingsley", "Lonzo",
         "Montell", "Napoleon", "Orlando", "Percival", "Quill", "Roderick",
         "Sylas", "Thaddeus", "Ulric", "Vance", "Wendell", "Xander", "Yusuf",
         "Zephyr", "Amari", "Brixton", "Cormac", "Dexter", "Everett"]
LAST = ["Ashford", "Bellweather", "Cartwright", "Dunmore", "Everhart",
        "Fairbanks", "Grimaldi", "Holloway", "Ironwood", "Jessup",
        "Kettleman", "Larkspur", "Mossgrove", "Northcott", "Ottoline",
        "Pendergast", "Quimby", "Rutherford", "Stonebridge", "Thackeray",
        "Underhill", "Vandermeer", "Whitlock", "Yarborough", "Ziegler",
        "Brightwater", "Copperfield", "Dellworth"]
COLLEGES = ["State Tech", "Coastal A&M", "Fairview", "Northern Poly",
            "Lakeside", "Ridgemont", "Cedar Valley", "Gulf Shore",
            "Highpoint", "Summit College", "Iron Range", "Bayou State"]

# ---------------------------------------------------------------------------
# Player factory. Every player gets a hidden `talent` used for search_rank
# ordering (board) and to bias weekly scoring so results feel coherent.
# ---------------------------------------------------------------------------
_pid = [4000]
players: dict[str, dict] = {}
stats: dict[str, dict] = {}
talent: dict[str, float] = {}


def _stat_line(pos: str, tal: float, gp: int) -> dict:
    """Position-relevant season-total stats keyed per views.STAT_SPECS."""
    scale = 0.4 + tal  # 0.4 .. 1.4
    st = {"gp": gp}
    if pos == "QB":
        st["pass_yd"] = round(gp * RNG.uniform(180, 300) * scale)
        st["pass_td"] = round(gp * RNG.uniform(0.8, 2.2) * scale)
        st["pass_int"] = round(gp * RNG.uniform(0.2, 0.9))
        st["rush_yd"] = round(gp * RNG.uniform(3, 30) * scale)
        st["rush_td"] = round(gp * RNG.uniform(0.0, 0.4) * scale)
        pts = st["pass_yd"] * 0.04 + st["pass_td"] * 4 - st["pass_int"] * 2 \
            + st["rush_yd"] * 0.1 + st["rush_td"] * 6
    elif pos == "RB":
        st["rush_yd"] = round(gp * RNG.uniform(30, 110) * scale)
        st["rush_td"] = round(gp * RNG.uniform(0.2, 1.1) * scale)
        st["rec_tgt"] = round(gp * RNG.uniform(1, 6) * scale)
        st["rec"] = round(st["rec_tgt"] * RNG.uniform(0.6, 0.85))
        st["rec_yd"] = round(st["rec"] * RNG.uniform(6, 11))
        st["rec_td"] = round(gp * RNG.uniform(0.0, 0.4) * scale)
        pts = st["rush_yd"] * 0.1 + st["rush_td"] * 6 + st["rec"] \
            + st["rec_yd"] * 0.1 + st["rec_td"] * 6
    elif pos == "WR":
        st["rec_tgt"] = round(gp * RNG.uniform(3, 11) * scale)
        st["rec"] = round(st["rec_tgt"] * RNG.uniform(0.55, 0.75))
        st["rec_yd"] = round(st["rec"] * RNG.uniform(9, 15))
        st["rec_td"] = round(gp * RNG.uniform(0.1, 0.9) * scale)
        st["rush_yd"] = round(gp * RNG.uniform(0, 6) * scale)
        pts = st["rec"] + st["rec_yd"] * 0.1 + st["rec_td"] * 6 + st["rush_yd"] * 0.1
    elif pos == "TE":
        st["rec_tgt"] = round(gp * RNG.uniform(2, 8) * scale)
        st["rec"] = round(st["rec_tgt"] * RNG.uniform(0.6, 0.8))
        st["rec_yd"] = round(st["rec"] * RNG.uniform(8, 13))
        st["rec_td"] = round(gp * RNG.uniform(0.1, 0.7) * scale)
        pts = st["rec"] + st["rec_yd"] * 0.1 + st["rec_td"] * 6
    elif pos == "K":
        st["fga"] = round(gp * RNG.uniform(1.2, 2.8))
        st["fgm"] = round(st["fga"] * RNG.uniform(0.75, 0.95))
        st["xpm"] = round(gp * RNG.uniform(1.5, 3.5))
        pts = st["fgm"] * 3 + st["xpm"]
    else:  # DEF
        st["sack"] = round(gp * RNG.uniform(1.5, 4.0) * scale)
        st["int"] = round(gp * RNG.uniform(0.3, 1.2) * scale)
        st["fum_rec"] = round(gp * RNG.uniform(0.2, 0.9))
        st["def_td"] = round(gp * RNG.uniform(0.0, 0.4) * scale)
        pts = st["sack"] + st["int"] * 2 + st["fum_rec"] * 2 + st["def_td"] * 6 + gp * 6
    st["pts_ppr"] = round(pts, 1)
    return st


def make_player(pos: str) -> str:
    _pid[0] += 1
    pid = str(_pid[0])
    tal = RNG.random()
    age = RNG.randint(21, 34)
    players[pid] = {
        "player_id": pid,
        "first_name": RNG.choice(FIRST),
        "last_name": RNG.choice(LAST),
        "position": pos,
        "team": RNG.choice(NFL_TEAMS),
        "age": age,
        "years_exp": RNG.randint(0, age - 21),
        "height": RNG.randint(68, 79),
        "weight": RNG.randint(180, 265),
        "college": RNG.choice(COLLEGES),
        "number": RNG.randint(1, 99),
        "espn_id": None,
        "status": "Active",
    }
    players[pid]["full_name"] = f"{players[pid]['first_name']} {players[pid]['last_name']}"
    talent[pid] = tal
    gp = RNG.randint(4, CURRENT_WEEK)
    stats[pid] = _stat_line(pos, tal, gp)
    return pid


def make_defense(abbr: str) -> str:
    """DEF entry keyed by team abbreviation (Sleeper convention)."""
    tal = RNG.uniform(0.3, 0.9)
    players[abbr] = {
        "player_id": abbr,
        "first_name": abbr,
        "last_name": "D/ST",
        "full_name": f"{abbr} D/ST",
        "position": "DEF",
        "team": abbr,
        "age": None,
        "years_exp": None,
    }
    talent[abbr] = tal
    stats[abbr] = _stat_line("DEF", tal, CURRENT_WEEK)
    return abbr


# ---------------------------------------------------------------------------
# Build 12 rosters. Composition per team: 2 QB, 4 RB, 4 WR, 2 TE, 1 K, 1 DEF
# = 14 players filling [QB,RB,RB,WR,WR,TE,FLEX,K,DEF] starters + 5 bench.
# ---------------------------------------------------------------------------
def_teams = RNG.sample(NFL_TEAMS, 12)
users = []
rosters = []
roster_starters: dict[int, list[str]] = {}
roster_players: dict[int, list[str]] = {}

for i in range(12):
    rid = i + 1
    uid = f"u{rid:02d}"
    users.append({
        "user_id": uid,
        "display_name": HANDLES[i],
        "avatar": None,
        "metadata": {"team_name": FRANCHISES[i]},
    })

    qbs = sorted((make_player("QB") for _ in range(2)), key=lambda p: -talent[p])
    rbs = sorted((make_player("RB") for _ in range(4)), key=lambda p: -talent[p])
    wrs = sorted((make_player("WR") for _ in range(4)), key=lambda p: -talent[p])
    tes = sorted((make_player("TE") for _ in range(2)), key=lambda p: -talent[p])
    kick = make_player("K")
    dst = make_defense(def_teams[i])

    flex_pool = sorted(rbs[2:] + wrs[2:] + tes[1:], key=lambda p: -talent[p])
    flex = flex_pool[0]
    starters = [qbs[0], rbs[0], rbs[1], wrs[0], wrs[1], tes[0], flex, kick, dst]

    all_ids = qbs + rbs + wrs + tes + [kick, dst]
    bench = [p for p in all_ids if p not in starters]
    roster_starters[rid] = starters
    roster_players[rid] = starters + bench

    rosters.append({
        "roster_id": rid,
        "owner_id": uid,
        "co_owners": None,
        "starters": starters,
        "players": starters + bench,
    })

# A handful of unrostered free agents so the board / free-agent pool has depth
# and transactions have realistic add targets.
free_agents = []
for _ in range(24):
    pos = RNG.choice(["QB", "RB", "WR", "WR", "TE", "K"])
    free_agents.append(make_player(pos))

# Global search_rank over every fantasy player, best talent = rank 1.
ranked = sorted(players.keys(), key=lambda p: -talent[p])
for rank, pid in enumerate(ranked, 1):
    players[pid]["search_rank"] = rank

# ---------------------------------------------------------------------------
# Round-robin schedule (circle method), first CURRENT_WEEK rounds. Generate
# weekly points biased by roster strength, tally records + points into rosters.
# ---------------------------------------------------------------------------
def round_robin(n: int) -> list[list[tuple[int, int]]]:
    arr = list(range(n))
    fixed, rot = arr[0], arr[1:]
    rounds = []
    for _ in range(n - 1):
        row = [fixed] + rot
        rounds.append([(row[k], row[n - 1 - k]) for k in range(n // 2)])
        rot = [rot[-1]] + rot[:-1]
    return rounds


def team_strength(rid: int) -> float:
    return sum(talent[p] for p in roster_starters[rid]) / len(roster_starters[rid])


schedule = round_robin(12)[:CURRENT_WEEK]
record = {rid: {"w": 0, "l": 0, "t": 0, "pf": 0.0, "pa": 0.0} for rid in range(1, 13)}
BASE = dt.datetime(2025, 9, 4, 13, 0, tzinfo=dt.timezone.utc)  # week-1 kickoff

for week, pairs in enumerate(schedule, 1):
    entries = []
    for mid, (ia, ib) in enumerate(pairs, 1):
        ra, rb = ia + 1, ib + 1
        pa = round(max(60.0, RNG.gauss(90 + 50 * team_strength(ra), 16)), 2)
        pb = round(max(60.0, RNG.gauss(90 + 50 * team_strength(rb), 16)), 2)
        for rid, pts, opp in ((ra, pa, pb), (rb, pb, pa)):
            record[rid]["pf"] += pts
            record[rid]["pa"] += opp
            if pts > opp:
                record[rid]["w"] += 1
            elif pts < opp:
                record[rid]["l"] += 1
            else:
                record[rid]["t"] += 1
            entries.append({
                "roster_id": rid,
                "matchup_id": mid,
                "points": pts,
                "starters": roster_starters[rid],
                "players": roster_players[rid],
            })
    entries.sort(key=lambda e: (e["matchup_id"], e["roster_id"]))
    (SEED_DIR / f"matchups_{week}.json").write_text(json.dumps(entries, indent=1))

# Fold tallied records + points into roster settings (fpts split int/decimal).
for r in rosters:
    rec = record[r["roster_id"]]
    pf, pa = round(rec["pf"], 2), round(rec["pa"], 2)
    r["settings"] = {
        "wins": rec["w"], "losses": rec["l"], "ties": rec["t"],
        "fpts": int(pf), "fpts_decimal": round((pf - int(pf)) * 100),
        "fpts_against": int(pa), "fpts_against_decimal": round((pa - int(pa)) * 100),
        "total_moves": RNG.randint(2, 14),
    }

# ---------------------------------------------------------------------------
# Transactions: a couple of completed moves per week (adds/drops/trades),
# referencing bench players and the free-agent pool so ids stay consistent.
# ---------------------------------------------------------------------------
for week in range(1, CURRENT_WEEK + 1):
    txns = []
    created = BASE + dt.timedelta(weeks=week - 1, days=2)
    for n in range(RNG.randint(1, 3)):
        ts = int((created + dt.timedelta(hours=n * 3)).timestamp() * 1000)
        rid = RNG.randint(1, 12)
        if RNG.random() < 0.25:  # trade between two teams
            other = RNG.choice([x for x in range(1, 13) if x != rid])
            give = roster_players[rid][-1]
            get = roster_players[other][-1]
            txns.append({
                "transaction_id": f"tx-{week}-{n}",
                "type": "trade", "status": "complete",
                "roster_ids": [rid, other],
                "adds": {give: other, get: rid},
                "drops": {give: rid, get: other},
                "created": ts, "week": week,
            })
        else:  # waiver / free-agent pickup dropping a bench player
            add = RNG.choice(free_agents)
            drop = roster_players[rid][-1]
            txns.append({
                "transaction_id": f"tx-{week}-{n}",
                "type": RNG.choice(["waiver", "free_agent"]), "status": "complete",
                "roster_ids": [rid],
                "adds": {add: rid},
                "drops": {drop: rid},
                "created": ts, "week": week,
            })
    (SEED_DIR / f"transactions_{week}.json").write_text(json.dumps(txns, indent=1))

# ---------------------------------------------------------------------------
# Top-level fixtures.
# ---------------------------------------------------------------------------
league = {
    "league_id": LEAGUE_ID,
    "name": "Poverty Franchises",
    "total_rosters": 12,
    "status": "in_season",
    "season": SEASON,
    "season_type": "regular",
    "sport": "nfl",
    "roster_positions": ROSTER_POSITIONS,
    "settings": {"num_teams": 12, "playoff_week_start": 15,
                 "playoff_teams": 6, "leg": CURRENT_WEEK},
    "scoring_settings": {"rec": 1.0, "pass_td": 4.0, "rush_td": 6.0},
}
nfl_state = {"week": CURRENT_WEEK, "season": SEASON, "season_type": "regular",
             "display_week": CURRENT_WEEK, "leg": CURRENT_WEEK,
             "season_start_date": "2025-09-04"}

(SEED_DIR / "league.json").write_text(json.dumps(league, indent=1))
(SEED_DIR / "users.json").write_text(json.dumps(users, indent=1))
(SEED_DIR / "rosters.json").write_text(json.dumps(rosters, indent=1))
(SEED_DIR / "players.json").write_text(json.dumps(players, indent=1))
(SEED_DIR / f"stats_{SEASON}.json").write_text(json.dumps(stats, indent=1))
(SEED_DIR / "nfl_state.json").write_text(json.dumps(nfl_state, indent=1))

print(f"wrote fixtures: {len(users)} users, {len(rosters)} rosters, "
      f"{len(players)} players, {CURRENT_WEEK} weeks of matchups/transactions")
