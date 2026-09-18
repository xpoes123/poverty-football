import json
import pathlib

import bot
from config import cfg


def test_announcement_embed_builders():
    prev = bot.build_preview_embed(3, [{"a": {"team": "A", "pct": 62.0}, "b": {"team": "B", "pct": 38.0}}])
    assert "Week 3" in prev.title and "62%" in prev.description
    dig = bot.build_digest_embed(2, [{"teams": ["A"], "adds": [("Star RB", "rb")], "drops": []}])
    assert "Week 2" in dig.title and "Star RB" in dig.description
    res = bot.build_results_embed({"week": 1, "lines": [], "extremes": None},
                                  awards=[{"award": "Team of the Week", "team": "A", "detail": "150 pts"}])
    assert any("Awards" in (f.name or "") for f in res.fields)


def test_effective_bot_leagues_default_is_single_league():
    lgs = cfg.effective_bot_leagues()
    assert len(lgs) == 1 and lgs[0]["league_id"] == cfg.league_id
    assert lgs[0]["channel_id"] == (cfg.results_channel_id or cfg.shame_channel_id)


def test_results_state_per_league_with_legacy_migration(tmp_path, monkeypatch):
    f = tmp_path / "results_state.json"
    monkeypatch.setattr(bot, "_RESULTS_STATE", f)
    # legacy single-league file → read as the default league's week
    f.write_text(json.dumps({"week": 3}))
    assert bot._last_announced_week(cfg.league_id) == 3
    assert bot._last_announced_week("999") == 0  # a different league isn't affected by legacy

    # marking drops the legacy scalar and keys per league
    bot._mark_announced("999", 5)
    d = json.loads(f.read_text())
    assert d == {cfg.league_id: 3, "999": 5}
    assert bot._last_announced_week(cfg.league_id) == 3 and bot._last_announced_week("999") == 5


def test_pulse_state_per_league_with_legacy_migration(tmp_path, monkeypatch):
    f = tmp_path / "pulse_state.json"
    monkeypatch.setattr(bot, "_PULSE_STATE", f)
    f.write_text(json.dumps({"week": 2, "final": 7}))  # legacy
    assert bot._pulse_state(cfg.league_id) == {"week": 2, "final": 7}
    assert bot._pulse_state("999") == {}
    bot._mark_pulse("999", 2, 4)
    d = json.loads(f.read_text())
    assert d[cfg.league_id] == {"week": 2, "final": 7} and d["999"] == {"week": 2, "final": 4}
    assert "week" not in d  # legacy scalar dropped
