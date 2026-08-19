import tempfile
from pathlib import Path

from h2h import (
    accept,
    all_wagers,
    american_profit,
    devig_two_way,
    games,
    matched_wagers,
    net_ledger,
    open_wagers,
    propose,
    settle,
)

# the-odds-api v4 shape: two normal games + one with no h2h market (skipped).
ODDS = [
    {"id": "g1", "commence_time": "2025-10-12T17:00:00Z",
     "home_team": "Kansas City Chiefs", "away_team": "Buffalo Bills",
     "bookmakers": [{"key": "dk", "title": "DK", "markets": [
         {"key": "h2h", "outcomes": [
             {"name": "Kansas City Chiefs", "price": -150},
             {"name": "Buffalo Bills", "price": 130}]}]}]},
    {"id": "g2", "commence_time": "2025-10-12T20:25:00Z",
     "home_team": "Miami Dolphins", "away_team": "Green Bay Packers",
     "bookmakers": [{"key": "fd", "title": "FD", "markets": [
         {"key": "h2h", "outcomes": [
             {"name": "Miami Dolphins", "price": 112},
             {"name": "Green Bay Packers", "price": -132}]}]}]},
    {"id": "g3", "commence_time": "2025-10-12T20:25:00Z",
     "home_team": "New York Jets", "away_team": "New England Patriots",
     "bookmakers": [{"key": "dk", "title": "DK", "markets": [
         {"key": "spreads", "outcomes": []}]}]},  # no h2h -> skipped
]


def test_devig_two_way():
    # -150/+130 has ~3.5% vig; devigged the two implied probs sum to 1 -> fair -138/+138
    assert devig_two_way(-150, 130) == (-138, 138)
    # symmetric pick'em stays a pick'em
    assert devig_two_way(-110, -110) == (100, 100)
    # favorite stays the favorite, underdog gets longer fair odds
    fav, dog = devig_two_way(-200, 170)
    assert fav < 0 < dog and fav > -200  # vig stripped -> less juice on the favorite


def test_games_parses_and_skips_no_h2h():
    rows = games(ODDS)
    assert len(rows) == 2  # g3 skipped
    g1 = rows[0]
    assert g1["game_id"] == "g1" and g1["home"] == "Kansas City Chiefs"
    assert g1["away"] == "Buffalo Bills"
    assert g1["home_price"] == -138 and g1["away_price"] == 138  # devigged from -150/+130
    assert g1["favorite"] == "Kansas City Chiefs"  # more-negative price
    assert rows[1]["favorite"] == "Green Bay Packers"  # away is the favorite


def test_american_profit_rounding():
    assert american_profit(100, 150) == 150
    assert american_profit(100, -200) == 50
    assert american_profit(50, 130) == 65
    assert american_profit(150, -150) == 100


def test_settle_statuses_and_payouts():
    wagers = [
        {"game_id": "g1", "side": "Kansas City Chiefs", "price": -150, "stake": 100,
         "proposer": 1, "acceptor": 2},   # win -> +profit
        {"game_id": "g2", "side": "Miami Dolphins", "price": 112, "stake": 100,
         "proposer": 3, "acceptor": 4},   # lost
        {"game_id": "g5", "side": "Anyone", "price": 100, "stake": 10,
         "proposer": 5, "acceptor": 6},   # matched, no result yet
        {"game_id": "g1", "side": "Buffalo Bills", "price": 130, "stake": 20,
         "proposer": 7, "acceptor": None},  # open, no acceptor
    ]
    results = {"g1": "Kansas City Chiefs", "g2": "Green Bay Packers"}
    out = {(w["game_id"], w["proposer"]): w for w in settle(wagers, results)}
    won = out[("g1", 1)]
    assert won["status"] == "won" and won["payout"] == american_profit(100, -150) == 67
    lost = out[("g2", 3)]
    assert lost["status"] == "lost" and lost["payout"] == -100
    matched = out[("g5", 5)]
    assert matched["status"] == "matched" and matched["payout"] == 0
    opn = out[("g1", 7)]
    assert opn["status"] == "open" and opn["payout"] == 0


def test_net_ledger_mirror():
    settled = [
        {"game_id": "g1", "side": "X", "price": -150, "stake": 100, "proposer": 1,
         "acceptor": 2, "payout": 67, "status": "won"},
        {"game_id": "g2", "side": "Y", "price": 100, "stake": 50, "proposer": 3,
         "acceptor": 4, "payout": -50, "status": "lost"},
        {"game_id": "g3", "side": "Z", "price": 100, "stake": 10, "proposer": 5,
         "acceptor": 6, "payout": 0, "status": "matched"},  # ignored
        {"game_id": "g4", "side": "W", "price": 100, "stake": 10, "proposer": 7,
         "acceptor": None, "payout": 0, "status": "open"},  # ignored
    ]
    net = net_ledger(settled)
    assert net[1] == 67 and net[2] == -67       # proposer won, acceptor mirrors
    assert net[3] == -50 and net[4] == 50       # proposer lost, acceptor mirrors
    assert 5 not in net and 6 not in net        # matched contributes nothing
    assert 7 not in net                         # open contributes nothing


def test_sqlite_roundtrip_and_guards():
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "h2h.db"
        propose(1, "g1", "Kansas City Chiefs", -150, 100, path=db)
        opens = open_wagers(path=db)
        assert len(opens) == 1 and opens[0]["proposer"] == 1
        assert opens[0]["acceptor"] is None
        wid = opens[0]["id"]

        # self-accept rejected
        try:
            accept(wid, 1, path=db)
            assert False, "self-accept should raise"
        except ValueError:
            pass

        # another roster accepts -> moves to matched
        accept(wid, 2, path=db)
        assert open_wagers(path=db) == []
        matched = matched_wagers(path=db)
        assert len(matched) == 1 and matched[0]["acceptor"] == 2

        # double-accept rejected
        try:
            accept(wid, 3, path=db)
            assert False, "double-accept should raise"
        except ValueError:
            pass

        assert len(all_wagers(path=db)) == 1

        # bad stakes rejected
        for bad in (0, -5, 3.5, "10", True):
            try:
                propose(9, "g2", "Miami Dolphins", 112, bad, path=db)
                assert False, f"expected {bad!r} to be rejected"
            except (ValueError, TypeError):
                pass
        assert len(all_wagers(path=db)) == 1  # nothing extra landed


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
    print("all passed")
