"""Pure join-check + shame-message logic. No network, no discord — fully testable."""

from dataclasses import dataclass
from datetime import date
import random


@dataclass
class Member:
    name: str
    sleeper: str
    discord_id: int | None = None  # None → shamed by name instead of a real @ping
    user_id: str | None = None  # filled by resolving `sleeper`; None = unresolved


def find_missing(members: list[Member], joined_ids: set[str]) -> list[Member]:
    """Anyone whose resolved user_id isn't in the joined set. Unresolved (None) counts
    as missing — a typo'd handle or someone who hasn't signed up at all."""
    return [m for m in members if m.user_id not in joined_ids]


def days_until_draft(draft_date: date | None, today: date) -> int | None:
    return None if draft_date is None else (draft_date - today).days


# tone tiers keyed by days-until-draft; each is a list of templates using {who}
TIERS = {
    "chill": [
        "Roster's still got holes. {who} — hop in when you get a sec.",
        "Poverty Franchises is filling up. {who}, you're not in yet 👀",
    ],
    "nudge": [
        "{who} — draft's coming up and you STILL haven't joined. Let's go.",
        "Gentle reminder that {who} is single-handedly holding up the league.",
    ],
    "harsh": [
        "Draft is basically here and {who} can't be bothered to click one link. Embarrassing.",
        "{who}: the league is waiting on YOU. This is your villain origin story.",
    ],
    "brutal": [
        "DRAFT DAY has passed the horizon and {who} is STILL ghosting. Absolute poverty behavior.",
        "{who} — at this point the shame is the only roster move you've made all season.",
    ],
    "mild": [  # used when no draft_date is set
        "{who} haven't joined Poverty Franchises yet. The seats are waiting.",
    ],
}


def bucket(days: int | None) -> str:
    if days is None:
        return "mild"
    if days < 0:
        return "brutal"
    if days < 3:
        return "harsh"
    if days <= 7:
        return "nudge"
    return "chill"


def _mention(m: Member) -> str:
    return f"<@{m.discord_id}>" if m.discord_id else f"**{m.name}**"


def shame_message(missing: list[Member], days: int | None) -> str:
    who = " ".join(_mention(m) for m in missing)
    template = random.choice(TIERS[bucket(days)])
    return template.format(who=who)
