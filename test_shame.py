from datetime import date

from shame import (
    Member,
    bucket,
    countdown_line,
    days_until_draft,
    find_missing,
    mention,
    missing_block,
    tier,
    tone_line,
    unpaid,
)


def test_find_missing():
    ms = [
        Member("Al", "al", user_id="100"),
        Member("Bo", "bo", user_id="200"),
        Member("Cy", "cy", user_id=None),  # unresolved handle
    ]
    missing = find_missing(ms, joined_ids={"100"})
    assert [m.name for m in missing] == ["Bo", "Cy"]  # Bo not joined, Cy unresolved


def test_unpaid_only_joined_and_not_paid():
    ms = [
        Member("Paid", "p", user_id="100", paid=True),      # joined + paid -> excluded
        Member("Owes", "o", user_id="200"),                 # joined, not paid -> shamed
        Member("NotIn", "n", user_id="300"),                # not joined -> can't owe yet
    ]
    owed = unpaid(ms, joined_ids={"100", "200"})
    assert [m.name for m in owed] == ["Owes"]


def test_days_until_draft():
    assert days_until_draft(None, date(2026, 8, 17)) is None
    assert days_until_draft(date(2026, 8, 20), date(2026, 8, 17)) == 3
    assert days_until_draft(date(2026, 8, 10), date(2026, 8, 17)) == -7


def test_bucket_boundaries():
    assert bucket(None) == "mild"
    assert bucket(-1) == "brutal"
    assert bucket(0) == "harsh"
    assert bucket(2) == "harsh"
    assert bucket(3) == "nudge"
    assert bucket(7) == "nudge"
    assert bucket(8) == "chill"


def test_mention_ping_vs_name():
    assert mention(Member("Al", "al", discord_id=111)) == "<@111>"
    assert mention(Member("Al", "al")) == "**Al**"  # no discord_id → name fallback


def test_missing_block_lists_each_on_its_own_line():
    ms = [Member("Al", "al", discord_id=111), Member("Bo", "bo")]
    block = missing_block(ms)
    assert "<@111>" in block and "**Bo**" in block
    assert block.count("\n") == 1  # two members → one newline between them


def test_tier_has_color_and_lines():
    t = tier(5)  # nudge
    assert isinstance(t["color"], int)
    assert tone_line(5) in t["lines"]


def test_countdown_line():
    assert countdown_line(None) == "📅 Draft not scheduled yet"
    assert "TODAY" in countdown_line(0)
    assert "**3** days" in countdown_line(3)
    assert "1 day ago" in countdown_line(-1)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
    print("all passed")
