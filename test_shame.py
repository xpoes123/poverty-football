from datetime import date

from shame import Member, bucket, days_until_draft, find_missing, shame_message


def test_find_missing():
    ms = [
        Member("Al", "al", 1, user_id="100"),
        Member("Bo", "bo", 2, user_id="200"),
        Member("Cy", "cy", 3, user_id=None),  # unresolved handle
    ]
    missing = find_missing(ms, joined_ids={"100"})
    assert [m.name for m in missing] == ["Bo", "Cy"]  # Bo not joined, Cy unresolved


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


def test_shame_message_pings_everyone():
    ms = [Member("Al", "al", 111, user_id=None), Member("Bo", "bo", 222, user_id=None)]
    msg = shame_message(ms, days=5)
    assert "<@111>" in msg and "<@222>" in msg


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
    print("all passed")
