from results import announcement, extremes, result_lines

# views.scoreboard-shaped fixtures
GAMES = [
    {"sides": [{"team": "A", "points": 120.0}, {"team": "B", "points": 100.0}], "winner": 0},
    {"sides": [{"team": "C", "points": 90.0}, {"team": "D", "points": 140.0}], "winner": 1},
    {"sides": [{"team": "E", "points": 110.0}, {"team": "F", "points": 110.0}], "winner": None},  # tie
    {"sides": [{"team": "G", "points": 0}, {"team": "H", "points": 0}], "winner": None},          # unplayed
]


def test_result_lines_sorted_by_margin_and_skips_unplayed():
    lines = result_lines(GAMES)
    assert len(lines) == 3  # unplayed dropped, tie kept
    assert lines[0]["winner"] == "D" and lines[0]["margin"] == 50.0  # biggest blowout first
    assert lines[1]["winner"] == "A" and lines[1]["margin"] == 20.0
    assert lines[2]["tie"] is True and {lines[2]["a"], lines[2]["b"]} == {"E", "F"}


def test_extremes_ignores_unplayed():
    ex = extremes(GAMES)
    assert ex["high_team"] == "D" and ex["high"] == 140.0
    assert ex["low_team"] == "C" and ex["low"] == 90.0  # G/H (0 pts) excluded


def test_announcement_none_when_nothing_final():
    unplayed = [{"sides": [{"team": "X", "points": 0}, {"team": "Y", "points": 0}], "winner": None}]
    assert announcement(unplayed, 5) is None
    a = announcement(GAMES, 6)
    assert a["week"] == 6 and len(a["lines"]) == 3


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
    print("all passed")
