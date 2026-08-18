import tempfile
from pathlib import Path

from betting import STARTING_BALANCE, all_bets, balances, matchup_result, place_bet, settle

# A seed-style week: matchup 1 = r1 (100) beats r7 (90); matchup 2 = r6 tie r8; unplayed r5/r9.
WEEK = [
    {"roster_id": 1, "matchup_id": 1, "points": 100.0},
    {"roster_id": 7, "matchup_id": 1, "points": 90.0},
    {"roster_id": 6, "matchup_id": 2, "points": 111.0},
    {"roster_id": 8, "matchup_id": 2, "points": 111.0},
    {"roster_id": 5, "matchup_id": 3, "points": 0},
    {"roster_id": 9, "matchup_id": 3, "points": 0},
]


def test_matchup_result_win_loss_tie_none():
    assert matchup_result(WEEK, 1) == "win"
    assert matchup_result(WEEK, 7) == "loss"
    assert matchup_result(WEEK, 6) == "tie"
    assert matchup_result(WEEK, 8) == "tie"
    assert matchup_result(WEEK, 5) is None   # both sides 0 → not played
    assert matchup_result(WEEK, 99) is None  # roster not in the week
    assert matchup_result([], 1) is None     # empty week (future)


def test_matchup_result_bye_is_none():
    week = [{"roster_id": 3, "matchup_id": 1, "points": 120.0}]  # lone side
    assert matchup_result(week, 3) is None


def test_settle_payout_signs_and_status():
    bets = [
        {"roster_id": 1, "week": 1, "stake": 50},
        {"roster_id": 7, "week": 1, "stake": 50},
        {"roster_id": 6, "week": 1, "stake": 50},
        {"roster_id": 5, "week": 1, "stake": 50},  # no result → open
    ]
    results = {(1, 1): "win", (7, 1): "loss", (6, 1): "tie"}
    out = {(b["roster_id"]): b for b in settle(bets, results)}
    assert out[1]["payout"] == 50 and out[1]["status"] == "won"
    assert out[7]["payout"] == -50 and out[7]["status"] == "lost"
    assert out[6]["payout"] == 0 and out[6]["status"] == "push"
    assert out[5]["payout"] == 0 and out[5]["status"] == "open"


def test_balances_rollup():
    settled = [
        {"roster_id": 1, "week": 1, "stake": 50, "payout": 50, "status": "won"},
        {"roster_id": 1, "week": 2, "stake": 30, "payout": -30, "status": "lost"},
        {"roster_id": 7, "week": 1, "stake": 50, "payout": -50, "status": "lost"},
        {"roster_id": 6, "week": 1, "stake": 50, "payout": 0, "status": "push"},
    ]
    bal = balances(settled)
    assert bal[1] == STARTING_BALANCE + 20   # +50 then -30
    assert bal[7] == STARTING_BALANCE - 50
    assert bal[6] == STARTING_BALANCE        # push nets 0


def test_place_bet_and_all_bets_roundtrip():
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "bets.db"
        place_bet(1, 6, 100, path=db)
        rows = all_bets(path=db)
        assert len(rows) == 1
        assert rows[0]["roster_id"] == 1 and rows[0]["week"] == 6 and rows[0]["stake"] == 100

        # one open bet per roster+week
        try:
            place_bet(1, 6, 25, path=db)
            assert False, "expected duplicate bet to be rejected"
        except ValueError:
            pass

        # bad stakes rejected
        for bad in (0, -5, 3.5, "10", True):
            try:
                place_bet(2, 6, bad, path=db)
                assert False, f"expected {bad!r} to be rejected"
            except (ValueError, TypeError):
                pass

        assert len(all_bets(path=db)) == 1  # nothing extra landed


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
    print("all passed")
