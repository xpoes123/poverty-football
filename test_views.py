from views import (draft_board, initials, luck_table, next_week_breakdown, playoff_odds,
                   positional_ranks, power_rankings, record_str, records_book, roster_view,
                   scoreboard, standings, team_name, team_schedule, weekly_awards, win_pct)


def test_trade_side_projections_and_starter_total():
    from views import trade_side
    players = {"a": {"full_name": "Stud", "position": "RB", "team": "SF"},
               "b": {"full_name": "Flex", "position": "WR", "team": "KC"},
               "c": {"full_name": "Benchie", "position": "TE", "team": "GB"}}
    roster = {"starters": ["a", "b"], "players": ["a", "b", "c"]}
    proj = {"a": 18.0, "b": 11.0, "c": 4.0}
    side = trade_side(roster, players, proj)
    assert [p["n"] for p in side["players"]] == ["Stud", "Flex", "Benchie"]  # sorted by proj
    assert side["players"][0]["starter"] and not side["players"][2]["starter"]
    assert side["starter_total"] == 29.0  # 18 + 11, bench excluded


def test_win_pct_and_scoring_model():
    import pytest
    from views import matchup_win_pct, scoring_model
    scores = {1: [120.0, 130.0, 125.0], 2: [90.0, 95.0, 92.0]}
    model = scoring_model(scores, {1, 2, 3})
    assert 120 < model[1][0] < 130            # team 1 mean from its history
    assert model[3][0] > 0                     # team 3 has no history → league-average fallback
    p = matchup_win_pct(model[1], model[2])    # strong team vs weak team
    assert p > 50 and matchup_win_pct(model[2], model[1]) == pytest.approx(100 - p, abs=0.2)
    assert matchup_win_pct((100, 20), (100, 20)) == 50.0  # equal teams → coin flip


def test_records_book_and_weekly_awards():
    users = [{"user_id": "u1", "display_name": "A"}, {"user_id": "u2", "display_name": "B"}]
    rosters = [{"roster_id": 1, "owner_id": "u1"}, {"roster_id": 2, "owner_id": "u2"}]
    wk1 = [{"roster_id": 1, "matchup_id": 1, "points": 130.0}, {"roster_id": 2, "matchup_id": 1, "points": 70.0}]
    wk2 = [{"roster_id": 1, "matchup_id": 1, "points": 95.0}, {"roster_id": 2, "matchup_id": 1, "points": 96.0}]
    rec = records_book([(1, wk1), (2, wk2)], users, rosters)
    assert rec["top_score"]["value"] == 130.0 and rec["top_score"]["week"] == 1
    assert rec["low_score"]["value"] == 70.0
    assert rec["blowout"]["margin"] == 60.0 and rec["blowout"]["winner"] == "A"
    assert rec["nailbiter"]["margin"] == 1.0 and rec["nailbiter"]["week"] == 2
    assert rec["streak"]["team"] == "A" and rec["streak"]["len"] == 1  # A won wk1, lost wk2

    players = {"p9": {"full_name": "Bench Beast", "position": "RB", "team": "SF"}}
    aw = weekly_awards(1, [
        {"roster_id": 1, "matchup_id": 1, "points": 130.0, "starters": ["s1"], "players_points": {"s1": 130.0, "p9": 40.0}},
        {"roster_id": 2, "matchup_id": 1, "points": 70.0, "starters": ["s2"], "players_points": {"s2": 70.0}},
    ], users, rosters, players)
    kinds = {a["award"]: a for a in aw}
    assert kinds["Team of the Week"]["team"] == "A"
    assert kinds["Cupcake"]["team"] == "B"
    assert "Bench Beast" in kinds["Bench Blunder"]["detail"]  # p9 benched, outscored nobody's starter but flagged


def test_power_rankings_orders_by_blend_and_tracks_movement():
    users = [{"user_id": "u1", "display_name": "A"}, {"user_id": "u2", "display_name": "B"},
             {"user_id": "u3", "display_name": "C"}]
    rosters = [{"roster_id": 1, "owner_id": "u1"}, {"roster_id": 2, "owner_id": "u2"},
               {"roster_id": 3, "owner_id": "u3"}]
    # week1: 1 crushes everyone; week2: 3 surges, 1 dips
    wk1 = [{"roster_id": 1, "points": 140}, {"roster_id": 2, "points": 100}, {"roster_id": 3, "points": 60}]
    wk2 = [{"roster_id": 1, "points": 80}, {"roster_id": 2, "points": 90}, {"roster_id": 3, "points": 150}]
    rows = power_rankings([wk1, wk2], users, rosters)
    assert [r["rank"] for r in rows] == [1, 2, 3]
    by = {r["roster_id"]: r for r in rows}
    # team 3 was worst after wk1, should climb after its wk2 surge (positive movement)
    assert by[3]["move"] > 0
    assert sum(r["move"] for r in rows) == 0  # movements net to zero


def test_playoff_odds_favors_the_stronger_undefeated_team():
    standings_rows = [
        {"roster_id": 1, "team": "Juggernaut", "avatar": None, "wins": 1, "losses": 0, "ties": 0, "pf": 150.0},
        {"roster_id": 2, "team": "Middling", "avatar": None, "wins": 0, "losses": 1, "ties": 0, "pf": 90.0},
        {"roster_id": 3, "team": "Weakling", "avatar": None, "wins": 0, "losses": 1, "ties": 0, "pf": 70.0},
        {"roster_id": 4, "team": "Average", "avatar": None, "wins": 1, "losses": 0, "ties": 0, "pf": 100.0},
    ]
    scores = {1: [150.0], 2: [90.0], 3: [70.0], 4: [100.0]}
    remaining = [(1, 2), (3, 4), (1, 3), (2, 4)]  # two more weeks
    odds = playoff_odds(scores, standings_rows, remaining, playoff_teams=2, sims=500)
    assert len(odds) == 4 and odds[0]["rank"] == 1
    by_id = {o["roster_id"]: o for o in odds}
    # the high-scoring 1-0 team should have the best odds; the low-scoring 0-1 team the worst
    assert by_id[1]["playoff_pct"] == max(o["playoff_pct"] for o in odds)
    assert by_id[3]["playoff_pct"] == min(o["playoff_pct"] for o in odds)
    assert all(0 <= o["playoff_pct"] <= 100 for o in odds)
    # deterministic (seeded): same inputs → same odds
    assert playoff_odds(scores, standings_rows, remaining, 2, sims=500)[0] == odds[0]


def test_next_week_breakdown_optimal_and_weaknesses():
    players = {
        "qb": {"full_name": "QB One", "position": "QB", "team": "KC"},
        "rb1": {"full_name": "RB One", "position": "RB", "team": "SF"},
        "rb2": {"full_name": "RB Two", "position": "RB", "team": "DAL"},  # benched but better
        "wr1": {"full_name": "WR One", "position": "WR", "team": "CIN"},
        "wrx": {"full_name": "WR Out", "position": "WR", "team": "BUF", "injury_status": "Out"},
        "te1": {"full_name": "TE One", "position": "TE", "team": "GB"},
    }
    # slots: QB, RB, WR, FLEX, TE (+ bench). Manager starts the weaker RB and an Out WR.
    positions = ["QB", "RB", "WR", "FLEX", "TE", "BN"]
    roster = {"roster_id": 1, "starters": ["qb", "rb1", "wrx", "rb2", "te1"],
              "players": ["qb", "rb1", "rb2", "wr1", "wrx", "te1"]}
    proj = {"qb": 20.0, "rb1": 8.0, "rb2": 14.0, "wr1": 12.0, "wrx": 0.0, "te1": 6.0}
    # a second roster so the league median exists
    other = {"roster_id": 2, "starters": ["wr1"], "players": ["wr1"]}
    b = next_week_breakdown(roster, [roster, other], players, proj, positions, week=3)

    # current = qb20 + rb1 8 + wrx 0 + rb2 14 + te1 6 = 48; optimal swaps in wr1(12) for wrx(0)
    assert b["current_total"] == 48.0
    assert b["optimal_total"] == 60.0
    assert b["points_left"] == 12.0
    kinds = {w["kind"] for w in b["weaknesses"]}
    assert "suboptimal" in kinds        # wr1 should start over wrx
    assert "inactive" in kinds          # wrx is Out
    assert "thin" in kinds              # no bench QB/TE backup


def test_draft_board_orders_by_search_rank_and_filters():
    players = {
        "a": {"full_name": "Star RB", "position": "RB", "team": "SF", "search_rank": 2, "age": 25, "years_exp": 3},
        "b": {"full_name": "Top WR", "position": "WR", "team": "CIN", "search_rank": 1, "age": 24, "years_exp": 0},
        "c": {"full_name": "No Team", "position": "RB", "team": None, "search_rank": 5},  # dropped: no team
        "d": {"full_name": "Bench Guy", "position": "OL", "team": "NYG", "search_rank": 9},  # dropped: non-fantasy
    }
    stats = {"b": {"pts_ppr": 403.0}}
    board = draft_board(players, stats)
    assert [r["name"] for r in board] == ["Top WR", "Star RB"]  # rank 1 before rank 2, others filtered
    assert board[0]["rank"] == 1 and board[0]["pts"] == "403"
    assert board[1]["pts"] == "—"  # no stats for Star RB
    assert [r["name"] for r in draft_board(players, stats, pos="WR")] == ["Top WR"]


def test_initials():
    assert initials("Retraso Mental") == "RM"
    assert initials("xpoes") == "XP"
    assert initials("") == "—"


def test_record_and_pct():
    assert record_str(2, 1, 0) == "2–1"
    assert record_str(2, 1, 1) == "2–1–1"
    assert win_pct(0, 0, 0) == "—"
    assert win_pct(2, 1, 0) == ".667"
    assert win_pct(3, 0, 0) == "1.000"


USERS = [
    {"user_id": "1", "display_name": "al", "metadata": {"team_name": "Alphas"}},
    {"user_id": "2", "display_name": "bo", "metadata": {}},
]
ROSTERS = [
    {"roster_id": 10, "owner_id": "1", "settings": {"wins": 1, "losses": 1, "fpts": 200, "fpts_decimal": 50}},
    {"roster_id": 20, "owner_id": "2", "settings": {"wins": 2, "losses": 0, "fpts": 180, "fpts_decimal": 0}},
    {"roster_id": 30, "owner_id": None, "settings": {}},  # unclaimed → excluded
]


def test_team_name_prefers_custom_then_display():
    assert team_name(USERS[0]) == "Alphas"
    assert team_name(USERS[1]) == "bo"
    assert team_name(None) == "Unclaimed"


def test_standings_sorted_by_wins_then_points_and_ranked():
    s = standings(USERS, ROSTERS)
    assert [r["owner"] for r in s] == ["bo", "al"]  # bo 2 wins outranks al 1 win
    assert s[0]["rank"] == 1 and s[1]["rank"] == 2
    assert len(s) == 2  # unclaimed roster dropped
    assert s[1]["pf"] == 200.5  # fpts + decimal/100


def test_scoreboard_pairs_by_matchup_id_and_flags_winner():
    matchups = [
        {"roster_id": 10, "matchup_id": 1, "points": 99.4},
        {"roster_id": 20, "matchup_id": 1, "points": 110.1},
    ]
    games = scoreboard(matchups, ROSTERS, USERS)
    assert len(games) == 1
    g = games[0]
    assert {s["team"] for s in g["sides"]} == {"Alphas", "bo"}
    assert g["winner"] == 1  # second side (110.1) wins


# Four claimed franchises + one unclaimed, for all-play luck math.
LUSERS = [
    {"user_id": "1", "display_name": "lucky", "metadata": {"team_name": "Lucky"}},
    {"user_id": "2", "display_name": "alice", "metadata": {}},
    {"user_id": "3", "display_name": "good", "metadata": {"team_name": "Good"}},
    {"user_id": "4", "display_name": "unlucky", "metadata": {"team_name": "Unlucky"}},
]
LROSTERS = [
    {"roster_id": 1, "owner_id": "1"},
    {"roster_id": 2, "owner_id": "2"},
    {"roster_id": 3, "owner_id": "3"},
    {"roster_id": 4, "owner_id": "4"},
    {"roster_id": 5, "owner_id": None},  # unclaimed → skipped
]


def _wk():
    # matchup 1: r1 (100) beats r2 (99); matchup 2: r3 (150) beats r4 (140)
    return [
        {"roster_id": 1, "matchup_id": 1, "points": 100.0},
        {"roster_id": 2, "matchup_id": 1, "points": 99.0},
        {"roster_id": 3, "matchup_id": 2, "points": 150.0},
        {"roster_id": 4, "matchup_id": 2, "points": 140.0},
    ]


def test_luck_table_all_play_and_luck_signs():
    rows = luck_table([_wk(), _wk()], LUSERS, LROSTERS)
    assert len(rows) == 4  # unclaimed roster skipped
    by = {r["roster_id"]: r for r in rows}

    good = by[3]  # tops the league every week → never out-scored
    assert good["all_play"] == "6–0"  # 3 other teams * 2 weeks, all beaten
    assert good["expected_wins"] == 2.0 and good["wins"] == 2
    assert good["luck"] == 0.0

    lucky = by[1]  # wins both head-to-heads but is 3rd-best on points
    assert lucky["wins"] == 2 and lucky["expected_wins"] == 0.7
    assert lucky["luck"] == 1.3  # more actual wins than expected → positive

    unlucky = by[4]  # 2nd-best on points but loses both head-to-heads
    assert unlucky["wins"] == 0 and unlucky["expected_wins"] == 1.3
    assert unlucky["luck"] == -1.3  # fewer actual wins than expected → negative

    assert [r["luck"] for r in rows] == sorted((r["luck"] for r in rows), reverse=True)
    assert rows[0]["rank"] == 1


def test_luck_table_empty_weeks():
    assert luck_table([], LUSERS, LROSTERS) == []


def test_luck_table_handles_ties():
    week = [
        {"roster_id": 1, "matchup_id": 1, "points": 111.0},
        {"roster_id": 2, "matchup_id": 1, "points": 111.0},  # equal → tie, no all-play credit
    ]
    rows = luck_table([week], LUSERS, LROSTERS)
    assert len(rows) == 2
    r = next(x for x in rows if x["roster_id"] == 1)
    assert r["ties"] == 1 and r["wins"] == 0 and r["losses"] == 0
    assert r["all_play"] == "0–0" and r["expected_wins"] == 0 and r["luck"] == 0.0
    assert r["avg_pts"] == 111.0


def test_positional_ranks_and_roster_view():
    players = {
        "1": {"position": "RB", "full_name": "Back One", "team": "ATL"},
        "2": {"position": "RB", "full_name": "Back Two", "team": "DET"},
        "3": {"position": "QB", "full_name": "Passer", "team": "WAS"},
    }
    scoring = {"rush_yd": 0.1, "pass_yd": 0.04}
    stats = {
        "1": {"rush_yd": 1000, "gp": 10},   # 100 pts, avg 10.0, best RB
        "2": {"rush_yd": 500, "gp": 10},    # 50 pts, avg 5.0, RB2
        "3": {"pass_yd": 4000, "gp": 16},   # 160 pts, avg 10.0, QB1
    }
    ranks = positional_ranks(players, stats, scoring)
    assert ranks == {"1": 1, "2": 2, "3": 1}

    roster = {"starters": ["3", "1"], "players": ["3", "1", "2"]}
    rv = roster_view(roster, players, stats, scoring, ["QB", "RB", "BN"], ranks)
    assert [s["slot"] for s in rv["starters"]] == ["QB", "RB"]
    assert rv["starters"][1]["avg"] == 10.0 and rv["starters"][1]["rank"] == "RB1"
    assert [b["pid"] for b in rv["bench"]] == ["2"]  # only non-starter


def test_team_schedule():
    by_week = {
        1: [{"roster_id": 1, "matchup_id": 1, "points": 120.0},
            {"roster_id": 2, "matchup_id": 1, "points": 100.0}],
        2: [{"roster_id": 1, "matchup_id": 1, "points": 90.0},
            {"roster_id": 2, "matchup_id": 1, "points": 110.0}],
    }
    sched = team_schedule(by_week, 1, LROSTERS, LUSERS)
    assert [g["week"] for g in sched] == [2, 1]  # most recent first
    assert sched[1]["result"] == "W" and sched[1]["my_pts"] == 120.0 and sched[1]["opp_pts"] == 100.0
    assert sched[0]["result"] == "L"


def test_draft_board_includes_defenses():
    # Sleeper gives defenses no search_rank; they must still appear (after ranked players).
    players = {
        "4046": {"full_name": "Patrick Q", "position": "QB", "team": "KC", "search_rank": 5},
        "PHI": {"full_name": "Philadelphia", "position": "DEF", "team": "PHI", "search_rank": None},
        "DAL": {"full_name": "Dallas", "position": "DEF", "team": "DAL", "search_rank": None},
    }
    stats = {"PHI": {"pts_ppr": 140}, "DAL": {"pts_ppr": 90}}
    rows = draft_board(players, stats)
    names = [r["name"] for r in rows]
    assert "Philadelphia" in names and "Dallas" in names       # defenses included
    assert names.index("Patrick Q") < names.index("Philadelphia")  # ranked players first
    assert names.index("Philadelphia") < names.index("Dallas")     # DEF ordered by points
    # the DEF-only chip is no longer empty
    assert [r["name"] for r in draft_board(players, stats, pos="DEF")] == ["Philadelphia", "Dallas"]


def test_kick_label():
    from views import kick_label
    assert kick_label("2025-10-17T00:15Z") == "Thu 8:15 PM"  # UTC -> Eastern
    assert kick_label(None) == "" and kick_label("garbage") == ""


def test_matchup_preview():
    from views import matchup_preview
    players = {"p1": {"position": "RB", "full_name": "Runner", "team": "ATL"},
               "p2": {"position": "WR", "full_name": "Catcher", "team": "DET"},  # out: no projection
               "p3": {"position": "QB", "full_name": "Thrower", "team": "KC"}}
    rosters = [{"roster_id": 1, "owner_id": "uA", "starters": ["p3", "p1"], "players": ["p3", "p1", "p2"]},
               {"roster_id": 2, "owner_id": "uB", "starters": ["p2"], "players": ["p2"]}]
    users = [{"user_id": "uA", "display_name": "A"}, {"user_id": "uB", "display_name": "B"}]
    week = [{"roster_id": 1, "matchup_id": 1}, {"roster_id": 2, "matchup_id": 1}]
    scoring = {"rush_yd": 0.1, "pass_yd": 0.04}
    projections = {"p1": {"rush_yd": 80}, "p3": {"pass_yd": 250}, "p2": {}}  # p2 empty -> out
    sched = {"ATL": {"opp": "NO", "at": "@", "kick": "2025-10-19T17:00Z"}}
    pv = matchup_preview(week, 1, rosters, users, players, ["QB", "RB", "BN"], projections, sched, scoring)
    assert pv["a"]["proj_total"] == 18.0  # 10 (pass) + 8 (rush)
    assert pv["a"]["starters"][1]["proj"] == 8.0 and pv["a"]["starters"][1]["game"].startswith("@ NO")
    assert pv["b"]["starters"][0]["proj"] == "—"  # empty projection -> out
    assert [b["pid"] for b in pv["a"]["bench"]] == ["p2"]  # non-starter listed on bench
    assert pv["a"]["bench"][0]["slot"] == "WR"  # bench slot = player's own position


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
    print("all passed")
