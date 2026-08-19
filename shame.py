"""Pure join-check + shame-presentation logic. No network, no discord — fully testable."""

from dataclasses import dataclass
from datetime import date
import random


@dataclass
class Member:
    name: str
    sleeper: str
    discord_id: int | None = None  # None → shamed by name instead of a real @ping
    paid: bool = False  # dues paid? unpaid joiners get payment-shamed
    user_id: str | None = None  # filled by resolving `sleeper`; None = unresolved


def find_missing(members: list[Member], joined_ids: set[str]) -> list[Member]:
    """Anyone whose resolved user_id isn't in the joined set. Unresolved (None) counts
    as missing — a typo'd handle or someone who hasn't signed up at all."""
    return [m for m in members if m.user_id not in joined_ids]


def unpaid(members: list[Member], joined_ids: set[str]) -> list[Member]:
    """Members who've joined but haven't paid dues (you can't owe until you're in)."""
    return [m for m in members if m.user_id in joined_ids and not m.paid]


def days_until_draft(draft_date: date | None, today: date) -> int | None:
    return None if draft_date is None else (draft_date - today).days


# per-tier: flavor line (description), embed color, and a title suffix
TIERS = {
    "chill": {
        "color": 0x2ECC71,
        "title": "Roster Check",
        "lines": [
            "Plenty of time left, but a few seats are still empty. 👀",
            "The league's filling up — just waiting on a couple stragglers.",
        ],
    },
    "nudge": {
        "color": 0xF1C40F,
        "title": "Draft's Coming",
        "lines": [
            "Draft's around the corner and some of you still haven't joined. Let's move.",
            "Friendly reminder that the draft won't wait for procrastinators.",
        ],
    },
    "harsh": {
        "color": 0xE67E22,
        "title": "Final Warning",
        "lines": [
            "Draft is basically here and these managers still can't click one link. Embarrassing.",
            "The clock is ticking and the following people are testing everyone's patience.",
        ],
    },
    "brutal": {
        "color": 0xE74C3C,
        "title": "Hall of Shame",
        "lines": [
            "Draft day has come and gone and these ghosts STILL haven't joined. Absolute poverty behavior.",
            "At this point the shame is the only roster move these managers have made.",
        ],
    },
    "mild": {
        "color": 0x3498DB,
        "title": "Roster Check",
        "lines": [
            "A few managers still haven't joined Poverty Franchises. The seats are waiting.",
        ],
    },
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


def mention(m: Member) -> str:
    return f"<@{m.discord_id}>" if m.discord_id else f"**{m.name}**"


def missing_block(missing: list[Member]) -> str:
    """One mention per line — reads far cleaner in an embed field than a run-on."""
    return "\n".join(f"‣ {mention(m)}" for m in missing)


def tier(days: int | None) -> dict:
    return TIERS[bucket(days)]


def tone_line(days: int | None) -> str:
    return random.choice(tier(days)["lines"])


def countdown_line(days: int | None) -> str:
    if days is None:
        return "📅 Draft not scheduled yet"
    if days < 0:
        return f"📅 Draft was {-days} day{'s' if days != -1 else ''} ago"
    if days == 0:
        return "📅 **Draft is TODAY**"
    return f"📅 Draft in **{days}** day{'s' if days != 1 else ''}"
