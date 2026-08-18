from views import draft_board, initials, lineup, record_str, scoreboard, standings, team_name, win_pct


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
    assert board[0]["rank"] == 1 and board[0]["pts"] == "403" and board[0]["exp"] == "R"
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


def test_lineup_maps_slots_then_bench():
    roster = {"starters": ["1", "2"], "players": ["1", "2", "3"]}
    players = {
        "1": {"full_name": "A B", "position": "QB", "team": "KC"},
        "2": {"full_name": "C D", "position": "RB", "team": "SF"},
        "3": {"full_name": "E F", "position": "WR", "team": "BUF"},
    }
    out = lineup(roster, players, ["QB", "RB", "BN"])
    assert [s["slot"] for s in out["starters"]] == ["QB", "RB"]
    assert out["starters"][0]["n"] == "A B" and out["starters"][0]["meta"] == "KC"
    assert [b["n"] for b in out["bench"]] == ["E F"]  # player 3 not a starter


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


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
    print("all passed")
