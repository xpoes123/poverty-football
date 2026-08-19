from h2h import games
from odds import _to_oddsapi_shape

SLATE = [{
    "game_id": "g1", "home_team": "Kansas City Chiefs", "away_team": "Buffalo Bills",
    "start_time": "2025-10-12T17:00:00Z",
    "odds": {"fanduel": {"ml_home": -120, "ml_away": 100},  # DK preferred over FD below
             "draftkings": {"ml_home": -150, "ml_away": 130,
                            "spread": -3.5, "spread_odds": -110, "spread_away": 3.5, "spread_away_odds": -110,
                            "total": 47.5, "total_over_odds": -110, "total_under_odds": -108}},
}]


def test_transform_prefers_dk_and_parses_all_markets():
    oa = _to_oddsapi_shape(SLATE)
    assert oa[0]["id"] == "g1" and oa[0]["bookmakers"][0]["key"] == "draftkings"  # DK chosen
    g = games(oa)[0]  # downstream parser is unchanged
    sels = {(s["market"], s["side"]) for s in g["selections"]}
    assert ("Moneyline", "Kansas City Chiefs") in sels
    assert ("Spread", "Kansas City Chiefs -3.5") in sels
    assert ("Total", "Over 47.5") in sels
    # moneyline devigged from DK's -150/+130 (not FD)
    ml = {s["side"]: s["price"] for s in g["selections"] if s["market"] == "Moneyline"}
    assert ml["Kansas City Chiefs"] == -138


def test_transform_skips_games_without_odds():
    assert _to_oddsapi_shape([{"game_id": "x", "home_team": "A", "away_team": "B", "odds": {}}]) == []
    # moneyline only (no spread/total fields) still yields a moneyline game
    ml_only = _to_oddsapi_shape([{"game_id": "y", "home_team": "A", "away_team": "B",
                                  "odds": {"betmgm": {"ml_home": -110, "ml_away": -110}}}])
    assert len(ml_only) == 1 and len(ml_only[0]["bookmakers"][0]["markets"]) == 1
