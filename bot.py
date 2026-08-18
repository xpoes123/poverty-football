import datetime as dt
import logging
import tomllib
from zoneinfo import ZoneInfo

import discord
from discord.ext import tasks

from config import cfg
from shame import (
    Member,
    countdown_line,
    days_until_draft,
    find_missing,
    missing_block,
    tier,
    tone_line,
)
from sleeper import get_joined_user_ids, get_league_meta, resolve_user_id

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("nfl-bot")

TZ = ZoneInfo(cfg.timezone)


def load_members() -> list[Member]:
    with open("expected.toml", "rb") as f:
        data = tomllib.load(f)
    return [Member(**m) for m in data["member"]]


async def compute_missing(members: list[Member]) -> tuple[list[Member], set[str]]:
    joined = await get_joined_user_ids(cfg.league_id)
    for m in members:
        m.user_id = await resolve_user_id(m.sleeper)
        if m.user_id is None:
            log.warning("could not resolve Sleeper handle %r (%s) — check expected.toml", m.sleeper, m.name)
    return find_missing(members, joined), joined


async def build_embed(missing: list[Member], joined_count: int) -> discord.Embed:
    name, total, status = await get_league_meta(cfg.league_id)
    days = days_until_draft(cfg.draft_date, dt.datetime.now(TZ).date())
    t = tier(days)
    e = discord.Embed(
        title=f"🏈 {name} — {t['title']}",
        url=cfg.join_url,  # makes the title clickable → the invite link
        description=f"{tone_line(days)}\n\n**[→ Join the league]({cfg.join_url})**",
        color=t["color"],
        timestamp=dt.datetime.now(TZ),
    )
    draft_val = countdown_line(days)
    if cfg.draft_date:
        draft_val += f"\n{cfg.draft_date:%b %-d}, {cfg.draft_time_label}"
    e.add_field(name=f"🚫 Still not in ({len(missing)})", value=missing_block(missing), inline=False)
    e.add_field(name="✅ Joined", value=f"**{joined_count}** / {total} seats", inline=True)
    e.add_field(name="⏱️ Draft", value=draft_val, inline=True)
    e.set_footer(text="Poverty Franchises")
    return e


class NflBot(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.default())

    async def on_ready(self):
        log.info("logged in as %s", self.user)
        # startup: log who's missing but DON'T ping — avoids spam on every restart
        missing, _ = await compute_missing(load_members())
        log.info("startup check: %d missing (%s)", len(missing), ", ".join(m.name for m in missing) or "none")
        if not self.daily_nag.is_running():
            self.daily_nag.start()

    @tasks.loop(time=dt.time(hour=cfg.check_hour, tzinfo=TZ))
    async def daily_nag(self):
        missing, joined = await compute_missing(load_members())
        if not missing:
            log.info("everyone's in — staying quiet")
            return
        channel = self.get_channel(cfg.shame_channel_id)
        if channel is None:
            log.error("shame channel %s not found", cfg.shame_channel_id)
            return
        embed = await build_embed(missing, len(joined))
        # content carries the pings so people get notified; embed is the pretty part
        await channel.send(content=" ".join(f"<@{m.discord_id}>" for m in missing if m.discord_id), embed=embed)
        log.info("shamed %d: %s", len(missing), ", ".join(m.name for m in missing))

    @daily_nag.before_loop
    async def _before(self):
        await self.wait_until_ready()


if __name__ == "__main__":
    NflBot().run(cfg.discord_token)
