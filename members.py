"""Read/write the shared expected.toml member list — the source of truth for dues + shame.

tomllib is read-only, so save() hand-serializes; the format is a flat list of tiny records.
Both nfl-web (the /admin editor) and nfl-bot read this same file from the project dir.
"""

import tomllib

MEMBERS_FILE = "expected.toml"
_HEADER = "# Poverty Football members — edited via the admin page. name / sleeper / discord_id / paid.\n"


def load() -> list[dict]:
    try:
        with open(MEMBERS_FILE, "rb") as f:
            return tomllib.load(f).get("member", [])
    except FileNotFoundError:
        return []


def _s(v: str) -> str:  # TOML basic string
    return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'


def save(members: list[dict]) -> None:
    out = [_HEADER]
    for m in members:
        block = ["[[member]]", f"name = {_s(m['name'])}", f"sleeper = {_s(m['sleeper'])}"]
        if m.get("discord_id") is not None:
            block.append(f"discord_id = {int(m['discord_id'])}")
        block.append(f"paid = {'true' if m.get('paid') else 'false'}")
        out.append("\n".join(block))
    with open(MEMBERS_FILE, "w") as f:
        f.write("\n\n".join(out) + "\n")


def _demo():
    import os, tempfile
    global MEMBERS_FILE
    MEMBERS_FILE = os.path.join(tempfile.mkdtemp(), "e.toml")
    save([{"name": 'Weird"\\name', "sleeper": "a", "discord_id": 5, "paid": True},
          {"name": "Plain", "sleeper": "b"}])
    m = load()  # round-trips through tomllib, so escaping must be valid
    assert m[0]["name"] == 'Weird"\\name' and m[0]["discord_id"] == 5 and m[0]["paid"] is True
    assert m[1].get("discord_id") is None and not m[1].get("paid", False)
    print("ok")


if __name__ == "__main__":
    _demo()
