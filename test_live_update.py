from live_update import roster_outlook, should_post


def test_roster_outlook_projection_and_remaining():
    starters = ["p1", "p2", "p3"]
    starter_points = [20.0, 5.0, 0.0]
    proj_pts = {"p1": 18, "p2": 12, "p3": 15}
    state = {"p1": "post", "p2": "in", "p3": "pre"}
    o = roster_outlook(starters, starter_points, 25.0, proj_pts, state)
    # p1 final -> lock 20; p2 in-progress -> max(5, 12)=12; p3 not started -> proj 15
    assert o["projected"] == 47.0
    assert o["current"] == 25.0
    assert o["left"] == 2                      # p2 (in) + p3 (pre) still going
    assert o["remaining"] == [("p3", 15.0)]    # only not-started players are "remaining"


def test_roster_outlook_missing_projection_defaults_zero():
    o = roster_outlook(["x"], [0.0], 0.0, {}, {"x": "pre"})
    assert o["projected"] == 0.0 and o["remaining"] == []


def test_should_post_only_when_a_slate_finished_and_games_remain():
    assert should_post(9, 16, 0) is True    # 1pm slate done, games remain -> post
    assert should_post(13, 16, 9) is True   # 4pm slate done -> post
    assert should_post(9, 16, 9) is False   # already posted at this count
    assert should_post(16, 16, 13) is False # all final -> results announcer handles it
    assert should_post(0, 16, 0) is False   # nothing final yet
