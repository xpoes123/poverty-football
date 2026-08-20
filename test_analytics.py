import analytics


def test_norm_path_collapses_ids():
    assert analytics.norm_path("/team/7") == "/team/:id"
    assert analytics.norm_path("/player/9509") == "/player/:id"
    assert analytics.norm_path("/game/401772940") == "/game/:id"
    assert analytics.norm_path("/matchup/6/1") == "/matchup/:wk/:id"
    assert analytics.norm_path("/draftboard") == "/draftboard"


def test_summary_aggregates(tmp_path):
    db = str(tmp_path / "a.db")
    analytics.record("/", "111", dbpath=db)
    analytics.record("/player/9509", "111", dbpath=db)
    analytics.record("/player/123", "222", dbpath=db)
    analytics.record("/", None, dbpath=db)  # anonymous
    s = analytics.summary({"111": "Dave", "222": "Rik"}, dbpath=db)
    assert s["total"] == 4 and s["unique"] == 2 and s["anon"] == 1
    pages = {p["path"]: p["views"] for p in s["pages"]}
    assert pages["/player/:id"] == 2 and pages["/"] == 2  # ids collapsed
    assert {v["name"]: v["views"] for v in s["visitors"]} == {"Dave": 2, "Rik": 1}  # named, sorted
    assert s["recent"][0]["path"] == "/" and s["recent"][0]["who"] == "anon"  # newest first


def test_record_never_raises_on_bad_db():
    analytics.record("/", "1", dbpath="/nonexistent/dir/that/cannot/be/made\0/x.db")  # swallowed
