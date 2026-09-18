from espn import _fnum, games, week_schedule

# Minimal ESPN scoreboard shape that games() consumes.
_SB = {
    "week": {"number": 2},
    "events": [{
        "id": "401", "shortName": "SF @ KC", "date": "2026-09-14T17:00Z",
        "competitions": [{
            "status": {"type": {"state": "post", "shortDetail": "Final", "description": "Final"}},
            "competitors": [
                {"homeAway": "home", "team": {"displayName": "Kansas City Chiefs", "abbreviation": "KC"}},
                {"homeAway": "away", "team": {"displayName": "San Francisco 49ers", "abbreviation": "SF"}},
            ],
        }],
    }],
}


def test_fnum_parses_and_defaults():
    assert _fnum("1,234") == 1234.0
    assert _fnum("12.5") == 12.5
    assert _fnum(None) == 0.0 and _fnum("nope") == 0.0


def test_games_parses_scoreboard():
    out = games(_SB)
    assert out["week"] == 2 and len(out["games"]) == 1
    g = out["games"][0]
    assert g["id"] == "401" and g["state"] == "post" and g["status"] == "Final"
    assert g["home"]["abbr"] == "KC" and g["away"]["abbr"] == "SF"


def test_games_empty_scoreboard():
    assert games({}) == {"games": [], "week": None}


def test_week_schedule_maps_both_teams():
    sched = week_schedule(_SB)
    assert sched["KC"] == {"opp": "SF", "at": "vs", "kick": "2026-09-14T17:00Z"}
    assert sched["SF"] == {"opp": "KC", "at": "@", "kick": "2026-09-14T17:00Z"}
