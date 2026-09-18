"""Read/write per-league member lists — the source of truth for dues + shame.

One file per league: the default (bot) league keeps `expected.toml`; others use
`expected.<league_id>.toml`. tomllib is read-only, so save() hand-serializes.
Top-level keys hold league settings (dues_amount, pay_url, draft_date,
draft_time_label); `[[member]]` tables hold the roster. Both nfl-web (/admin) and
nfl-bot read these files from the project dir.
"""

import os
import tomllib

from config import cfg

DEFAULT_FILE = "expected.toml"
_META_KEYS = ("dues_amount", "pay_url", "draft_date", "draft_time_label")


def path_for(league_id: str | None = None) -> str:
    league_id = league_id or cfg.league_id
    return DEFAULT_FILE if league_id == cfg.league_id else f"expected.{league_id}.toml"


def _read(league_id: str | None) -> dict:
    try:
        with open(path_for(league_id), "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        return {}


def load(league_id: str | None = None) -> list[dict]:
    return _read(league_id).get("member", [])


def load_meta(league_id: str | None = None) -> dict:
    d = _read(league_id)
    return {k: d[k] for k in _META_KEYS if k in d}


def _s(v: str) -> str:  # TOML basic string
    return '"' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '"'


def save(members: list[dict], meta: dict | None = None, league_id: str | None = None) -> None:
    meta = meta or {}
    out = ["# League members + dues, edited via the /admin page. Top keys are league settings.\n"]
    settings = []
    if meta.get("dues_amount") not in (None, ""):
        settings.append(f"dues_amount = {float(meta['dues_amount'])}")
    for k in ("pay_url", "draft_date", "draft_time_label"):
        if meta.get(k):
            settings.append(f"{k} = {_s(meta[k])}")
    if settings:
        out.append("\n".join(settings))
    for m in members:
        block = ["[[member]]", f"name = {_s(m['name'])}", f"sleeper = {_s(m['sleeper'])}"]
        if m.get("discord_id") is not None:
            block.append(f"discord_id = {int(m['discord_id'])}")
        block.append(f"paid = {'true' if m.get('paid') else 'false'}")
        out.append("\n".join(block))
    # atomic: write a temp file then rename, so a crash mid-write can't corrupt the roster
    path = path_for(league_id)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write("\n\n".join(out) + "\n")
    os.replace(tmp, path)


def _demo():
    import tempfile
    global DEFAULT_FILE
    DEFAULT_FILE = os.path.join(tempfile.mkdtemp(), "e.toml")
    save([{"name": 'Weird"\\name', "sleeper": "a", "discord_id": 5, "paid": True},
          {"name": "Plain", "sleeper": "b"}],
         meta={"dues_amount": 50, "pay_url": "https://venmo.com/x", "draft_date": "2026-09-05"})
    m, meta = load(), load_meta()  # round-trips through tomllib, so escaping must be valid
    assert m[0]["name"] == 'Weird"\\name' and m[0]["discord_id"] == 5 and m[0]["paid"] is True
    assert m[1].get("discord_id") is None and not m[1].get("paid", False)
    assert meta["dues_amount"] == 50.0 and meta["pay_url"] == "https://venmo.com/x"
    assert meta["draft_date"] == "2026-09-05"
    print("ok")


if __name__ == "__main__":
    _demo()
