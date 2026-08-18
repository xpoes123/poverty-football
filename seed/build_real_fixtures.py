"""Regenerate the dev-seed fixtures with REAL Sleeper players (real ids → real names,
headshots, 2025 stats) drafted in a realistic snake order. Run once; commit the output.

    ./.venv/bin/python seed/build_real_fixtures.py
"""
import json
import pathlib
from collections import defaultdict

import httpx

SEED = pathlib.Path(__file__).parent
B = "https://api.sleeper.app/v1"
FANTASY = {"QB", "RB", "WR", "TE", "K", "DEF"}
TEAMS = 12
TARGETS = {"QB": 2, "RB": 4, "WR": 4, "TE": 2, "K": 1, "DEF": 1}  # per team → 14

players = httpx.get(f"{B}/players/nfl", timeout=60).json()
stats = httpx.get(f"{B}/stats/nfl/regular/2025", timeout=60).json()


def rank(pid):
    return players[pid].get("search_rank") or 999999


pool = sorted(
    (pid for pid, p in players.items()
     if p.get("search_rank") and p["search_rank"] < 100000
     and p.get("position") in FANTASY and (p.get("team") or p.get("position") == "DEF")),
    key=rank,
)
# Best-player-available snake draft (rank order), so rounds interleave positions like
# a real draft. 12 skill rounds (QB≤2, TE≤3), then K (rd 13) and DEF (rd 14). Late-round
# nudges guarantee every team lands a QB and a TE for a valid lineup.
skill = [pid for pid in pool if players[pid]["position"] in ("QB", "RB", "WR", "TE")]
kickers = sorted((pid for pid, p in players.items()
                  if p.get("position") == "K" and p.get("team")), key=rank)
defenses = sorted((pid for pid, p in players.items() if p.get("position") == "DEF"), key=rank)
team_pids = {t: [] for t in range(TEAMS)}
cnt = {t: defaultdict(int) for t in range(TEAMS)}
draft = []  # (round, team_index, pid) in pick order


def _snake(rnd):
    return list(range(TEAMS)) if rnd % 2 else list(reversed(range(TEAMS)))


for rnd in range(1, 13):
    for t in _snake(rnd):
        force_qb = cnt[t]["QB"] == 0 and rnd >= 11
        force_te = cnt[t]["TE"] == 0 and rnd >= 12
        pick = None
        for pid in skill:
            pos = players[pid]["position"]
            if pos == "QB" and cnt[t]["QB"] >= 2:
                continue
            if pos == "TE" and cnt[t]["TE"] >= 3:
                continue
            if force_qb and pos != "QB":
                continue
            if force_te and pos != "TE":
                continue
            pick = pid
            break
        skill.remove(pick)
        team_pids[t].append(pick)
        cnt[t][players[pick]["position"]] += 1
        draft.append((rnd, t, pick))
for pos, src, rnd in (("K", kickers, 13), ("DEF", defenses, 14)):
    for t in _snake(rnd):
        pid = src.pop(0)
        team_pids[t].append(pid)
        draft.append((rnd, t, pid))


def starters(pids):
    """Fill QB,RB,RB,WR,WR,TE,FLEX,K,DEF from the roster's best players."""
    bp = defaultdict(list)
    for pid in sorted(pids, key=rank):
        bp[players[pid]["position"]].append(pid)
    take = lambda pos: bp[pos].pop(0) if bp[pos] else "0"
    line = [take("QB"), take("RB"), take("RB"), take("WR"), take("WR"), take("TE")]
    flex = sorted((pid for pos in ("RB", "WR", "TE") for pid in bp[pos]), key=rank)
    if flex:
        bp[players[flex[0]]["position"]].remove(flex[0])
    line += [flex[0] if flex else "0", take("K"), take("DEF")]
    return line


old = sorted(json.load(open(SEED / "rosters.json")), key=lambda r: r["roster_id"])
new_rosters = [{**r, "starters": starters(team_pids[i]), "players": team_pids[i]}
               for i, r in enumerate(old)]
json.dump(new_rosters, open(SEED / "rosters.json", "w"))

# players.json + stats: everything rostered, plus ~140 top free agents for Rankings/FA depth
keep = {pid for pids in team_pids.values() for pid in pids}
keep.update([pid for pid in pool if pid not in keep][:140])
json.dump({pid: players[pid] for pid in keep}, open(SEED / "players.json", "w"))
json.dump({pid: stats[pid] for pid in keep if pid in stats}, open(SEED / "stats_2025.json", "w"))

# draft picks from the recorded BPA pick order (team index t → roster_id t+1)
owner = {r["roster_id"]: r["owner_id"] for r in new_rosters}
picks = [{"round": rnd, "pick_no": i + 1, "draft_slot": t + 1,
          "roster_id": t + 1, "picked_by": owner[t + 1], "player_id": pid}
         for i, (rnd, t, pid) in enumerate(draft)]
json.dump(picks, open(SEED / "draft_picks.json", "w"))

# enrich weekly matchups: real starters + a plausible per-starter points split summing to the total
league = json.load(open(SEED / "league.json"))
slots = [p for p in league["roster_positions"] if p != "BN"]
SLOT_W = {"QB": 1.35, "RB": 1.25, "WR": 1.15, "TE": 0.9, "FLEX": 1.1, "K": 0.7, "DEF": 0.85}
rmap = {r["roster_id"]: r for r in new_rosters}
for f in SEED.glob("matchups_*.json"):
    ms = json.load(open(f))
    for m in ms:
        r = rmap[m["roster_id"]]
        m["starters"], m["players"] = r["starters"], r["players"]
        w = [SLOT_W.get(slots[i] if i < len(slots) else "FLEX", 1.0) for i in range(len(r["starters"]))]
        tot, sw = m.get("points") or 0, sum(w) or 1
        sp = [round(tot * wi / sw, 2) for wi in w]
        sp[-1] = round(tot - sum(sp[:-1]), 2)  # absorb rounding drift
        m["starters_points"] = sp
    json.dump(ms, open(f, "w"))

# remap transaction player ids to real ones so the feed shows real names
realpool = list(keep)
i = 0
for f in SEED.glob("transactions_*.json"):
    txns = json.load(open(f))
    for t in txns:
        for field in ("adds", "drops"):
            if t.get(field):
                remapped = {}
                for _, rid in t[field].items():
                    remapped[realpool[i % len(realpool)]] = rid
                    i += 1
                t[field] = remapped
    json.dump(txns, open(f, "w"))

print(f"{len(picks)} picks, {len(keep)} players; sample R1: "
      + ", ".join(f'{players[p["player_id"]]["full_name"]}({players[p["player_id"]]["position"]})'
                  for p in picks[:5]))
