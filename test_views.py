from views import scoreboard, standings, team_name


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
