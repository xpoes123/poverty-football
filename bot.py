import datetime as dt
import logging
import tomllib
from zoneinfo import ZoneInfo

import discord
from discord.ext import tasks

from config import cfg
from shame import Member, days_until_draft, find_missing, shame_message
from sleeper import get_joined_user_ids, resolve_user_id

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("nfl-bot")

TZ = ZoneInfo(cfg.timezone)


def load_members() -> list[Member]:
    with open("expected.toml", "rb") as f:
        data = tomllib.load(f)
    return [Member(**m) for m in data["member"]]


async def compute_missing(members: list[Member]) -> list[Member]:
    joined = await get_joined_user_ids(cfg.league_id)
    for m in members:
        m.user_id = await resolve_user_id(m.sleeper)
        if m.user_id is None:
            log.warning("could not resolve Sleeper handle %r (%s) — check expected.toml", m.sleeper, m.name)
    return find_missing(members, joined)


class NflBot(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.default())

    async def on_ready(self):
        log.info("logged in as %s", self.user)
        # startup: log who's missing but DON'T ping — avoids spam on every restart
        missing = await compute_missing(load_members())
        log.info("startup check: %d missing (%s)", len(missing), ", ".join(m.name for m in missing) or "none")
        if not self.daily_nag.is_running():
            self.daily_nag.start()

    @tasks.loop(time=dt.time(hour=cfg.check_hour, tzinfo=TZ))
    async def daily_nag(self):
        missing = await compute_missing(load_members())
        if not missing:
            log.info("everyone's in — staying quiet")
            return
        days = days_until_draft(cfg.draft_date, dt.datetime.now(TZ).date())
        channel = self.get_channel(cfg.shame_channel_id)
        if channel is None:
            log.error("shame channel %s not found", cfg.shame_channel_id)
            return
        await channel.send(shame_message(missing, days))
        log.info("shamed %d: %s", len(missing), ", ".join(m.name for m in missing))

    @daily_nag.before_loop
    async def _before(self):
        await self.wait_until_ready()


if __name__ == "__main__":
    NflBot().run(cfg.discord_token)
