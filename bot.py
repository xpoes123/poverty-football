import datetime as dt
import logging
import tomllib
from zoneinfo import ZoneInfo

import discord
from discord.ext import tasks

import json
import pathlib
import re

import espn
import h2h
import live_update
import results
from config import cfg
from shame import (
    Member,
    countdown_line,
    days_until_draft,
    find_missing,
    missing_block,
    tier,
    tone_line,
    unpaid,
)
from sleeper import (
    get_claimed_team_count,
    get_joined_user_ids,
    get_league,
    get_league_meta,
    get_matchups,
    get_nfl_state,
    get_players,
    get_projections,
    get_rosters,
    get_users,
    resolve_user_id,
)
from views import ESPN_TO_SLEEPER_TEAM, fantasy_points, player_line, scoreboard, team_name

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("nfl-bot")

TZ = ZoneInfo(cfg.timezone)


def load_members() -> list[Member]:
    with open("expected.toml", "rb") as f:
        data = tomllib.load(f)
    return [Member(**m) for m in data["member"]]


_RESULTS_STATE = pathlib.Path(__file__).parent / "data" / "results_state.json"


def _last_announced_week() -> int:
    try:
        return json.loads(_RESULTS_STATE.read_text()).get("week", 0)
    except (FileNotFoundError, ValueError):
        return 0


def _mark_announced(week: int) -> None:
    _RESULTS_STATE.parent.mkdir(parents=True, exist_ok=True)
    _RESULTS_STATE.write_text(json.dumps({"week": week}))


async def latest_complete_week() -> int | None:
    """The most-recently-finished regular-season week (during week N, N-1 is final)."""
    state = await get_nfl_state()
    if state.get("season_type") != "regular":
        return None
    complete = (state.get("week") or 1) - 1
    return complete if complete >= 1 else None


_MD_SPECIAL = re.compile(r"([*_`~|\\<>])")


def _safe(text: object) -> str:
    """Neutralize untrusted text (manager-set team names) for Discord markdown: escape
    formatting chars, defang mentions and auto-links."""
    s = _MD_SPECIAL.sub(r"\\\1", str(text))
    return s.replace("@", "@​").replace("://", ":/​/")


def build_results_embed(ann: dict) -> discord.Embed:
    # stacked, short lines that wrap cleanly on mobile — no monospace alignment
    blocks = []
    for r in ann["lines"]:
        if r.get("tie"):
            blocks.append(f"🤝 **{_safe(r['a'])}** {r['pa']:.1f}\n🤝 **{_safe(r['b'])}** {r['pb']:.1f}")
        else:
            blocks.append(f"🏆 **{_safe(r['winner'])}** {r['ws']:.1f}\n　{_safe(r['loser'])} {r['ls']:.1f}")
    desc = "\n\n".join(blocks)
    ex = ann.get("extremes")
    if ex:
        desc += (f"\n\n**High** {ex['high']:.1f} · {_safe(ex['high_team'])}"
                 f"\n**Low** {ex['low']:.1f} · {_safe(ex['low_team'])}")
    e = discord.Embed(title=f"🏈 Week {ann['week']} Results", color=0xC9A05E,
                      description=desc, timestamp=dt.datetime.now(TZ))
    e.set_footer(text="Poverty Franchises")
    return e


_PULSE_STATE = pathlib.Path(__file__).parent / "data" / "pulse_state.json"


def _pulse_state() -> dict:
    try:
        return json.loads(_PULSE_STATE.read_text())
    except (FileNotFoundError, ValueError):
        return {}


def _mark_pulse(week: int, final_count: int) -> None:
    _PULSE_STATE.parent.mkdir(parents=True, exist_ok=True)
    _PULSE_STATE.write_text(json.dumps({"week": week, "final": final_count}))


def build_pulse_embed(week: int, final_now: int, total: int, pairs: list, watch: list) -> discord.Embed:
    blocks = []
    for a, b in pairs:  # a = current leader (higher projection)
        blocks.append(f"**{_safe(a['team'])}** now {a['current']:.1f}, proj {a['projected']:.1f}\n"
                      f"{_safe(b['team'])} now {b['current']:.1f}, proj {b['projected']:.1f}")
    desc = "\n\n".join(blocks)
    if watch:
        watch_str = " · ".join(f"{_safe(n)} ({_safe(t)}, {p:.0f})" for n, p, t in watch)
        desc += f"\n\n👀 **Still to play:** {watch_str}"
    left = total - final_now
    e = discord.Embed(title=f"🏈 Week {week} · {final_now}/{total} games final",
                      description=desc, color=0xC9A05E, timestamp=dt.datetime.now(TZ))
    e.set_footer(text=f"{left} NFL game{'' if left == 1 else 's'} still to play")
    return e


async def roster_and_team_for_discord(discord_id: int) -> tuple[int, str] | tuple[None, None]:
    """Map a Discord user to their league roster_id + team name (owner or co-owner). None if unlinked."""
    m = next((m for m in load_members() if m.discord_id == discord_id), None)
    if m is None:
        return None, None
    uid = await resolve_user_id(m.sleeper)
    if uid is None:
        return None, None
    users = await get_users(cfg.league_id)
    rosters = await get_rosters(cfg.league_id)
    r = next((r for r in rosters
              if r.get("owner_id") == uid or uid in (r.get("co_owners") or [])), None)
    if r is None:
        return None, None
    by_id = {u["user_id"]: u for u in users}
    return r["roster_id"], team_name(by_id.get(r.get("owner_id")))


async def compute_missing(members: list[Member]) -> list[Member]:
    joined = await get_joined_user_ids(cfg.league_id)
    for m in members:
        m.user_id = await resolve_user_id(m.sleeper)
        if m.user_id is None:
            log.warning("could not resolve Sleeper handle %r (%s) — check expected.toml", m.sleeper, m.name)
    return find_missing(members, joined)


async def build_embed(missing: list[Member], owed: list[Member]) -> discord.Embed:
    name, total, status = await get_league_meta(cfg.league_id)
    teams = await get_claimed_team_count(cfg.league_id)
    days = days_until_draft(cfg.draft_date, dt.datetime.now(TZ).date())
    t = tier(days)
    e = discord.Embed(
        title=f"🏈 {name} — {t['title']}",
        url=cfg.join_url,  # makes the title clickable → the invite link
        description=f"{tone_line(days)}\n\n**[Join the league]({cfg.join_url})**",
        color=t["color"],
        timestamp=dt.datetime.now(TZ),
    )
    draft_val = countdown_line(days)
    if cfg.draft_date:
        draft_val += f"\n{cfg.draft_date:%b %-d}, {cfg.draft_time_label}"
    if missing:
        e.add_field(name=f"🚫 Still not in ({len(missing)})", value=missing_block(missing), inline=False)
    if owed:
        e.add_field(name=f"💸 Haven't paid ({len(owed)})", value=missing_block(owed), inline=False)
    e.add_field(name="✅ Teams in", value=f"**{teams}** / {total}", inline=True)
    e.add_field(name="⏱️ Draft", value=draft_val, inline=True)
    e.set_footer(text="Poverty Franchises")
    return e


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
        if not self.results_announcer.is_running():
            self.results_announcer.start()
        if not self.matchup_pulse.is_running():
            self.matchup_pulse.start()

    async def on_interaction(self, interaction: discord.Interaction):
        """Handle the 'Claim' button on a proposed h2h bet (message posted by the web app)."""
        if interaction.type != discord.InteractionType.component:
            return
        cid = (interaction.data or {}).get("custom_id", "")
        if not cid.startswith("claim:"):
            return
        try:
            wager_id = int(cid.split(":", 1)[1])
        except ValueError:
            return
        rid, team = await roster_and_team_for_discord(interaction.user.id)
        if rid is None:
            await interaction.response.send_message(
                "You're not linked to a league team, so you can't claim this.", ephemeral=True)
            return
        try:
            h2h.accept(wager_id, rid)
        except ValueError:
            await interaction.response.send_message(
                "That one's already claimed, or it's your own bet.", ephemeral=True)
            return
        embed = interaction.message.embeds[0] if interaction.message.embeds else None
        await interaction.response.edit_message(content=f"🤝 Claimed by **{team}**", embed=embed, view=None)
        log.info("h2h wager %d claimed by %s (roster %s)", wager_id, interaction.user, rid)

    @tasks.loop(time=dt.time(hour=cfg.check_hour, tzinfo=TZ))
    async def daily_nag(self):
        if cfg.nag_start_date and dt.datetime.now(TZ).date() < cfg.nag_start_date:
            log.info("before nag_start_date (%s) — skipping", cfg.nag_start_date)
            return
        members = load_members()
        missing = await compute_missing(members)  # resolves user_ids as a side effect
        owed = unpaid(members, await get_joined_user_ids(cfg.league_id))
        if not missing and not owed:
            log.info("everyone's in and paid up — staying quiet")
            return
        channel = self.get_channel(cfg.shame_channel_id)
        if channel is None:
            log.error("shame channel %s not found", cfg.shame_channel_id)
            return
        embed = await build_embed(missing, owed)
        # content carries the pings so people get notified; embed is the pretty part
        pings = " ".join(f"<@{m.discord_id}>" for m in (missing + owed) if m.discord_id)
        await channel.send(content=pings, embed=embed)
        log.info("shamed — missing %d, unpaid %d", len(missing), len(owed))

    @tasks.loop(time=dt.time(hour=10, tzinfo=TZ))
    async def results_announcer(self):
        """Once a week's matchups are final, post the results — once."""
        week = await latest_complete_week()
        if week is None or week <= _last_announced_week():
            return
        matchups = await get_matchups(cfg.league_id, week)
        if not matchups:
            return
        users, rosters = await get_users(cfg.league_id), await get_rosters(cfg.league_id)
        ann = results.announcement(scoreboard(matchups, rosters, users), week)
        if ann is None:
            return  # nothing decided yet
        cid = cfg.results_channel_id or cfg.shame_channel_id
        channel = self.get_channel(cid)
        if channel is None:
            log.error("results channel %s not found", cid)
            return
        await channel.send(embed=build_results_embed(ann))
        _mark_announced(week)
        log.info("announced week %d results (%d matchups)", week, len(ann["lines"]))

    @tasks.loop(minutes=30)
    async def matchup_pulse(self):
        """During game days, once a slate of NFL games goes final, post a live matchup update."""
        state = await get_nfl_state()
        if state.get("season_type") != "regular":
            return
        week, season = state.get("week") or 1, state.get("season") or "2025"
        try:
            espn_games = espn.games(await espn.scoreboard(year=int(season), week=week))["games"]
        except Exception:
            return
        if not espn_games:
            return
        total = len(espn_games)
        final_now = sum(1 for g in espn_games if g.get("state") == "post")
        prev = _pulse_state()
        posted = prev.get("final", 0) if prev.get("week") == week else 0
        if not live_update.should_post(final_now, total, posted):
            return  # no new slate finished (cheap check — avoids the projection fetch below)

        matchups = await get_matchups(cfg.league_id, week)
        if not matchups:
            return
        users, rosters, players = await get_users(cfg.league_id), await get_rosters(cfg.league_id), await get_players()
        scoring = (await get_league(cfg.league_id)).get("scoring_settings") or {}
        proj_pts = {pid: fantasy_points(st, scoring) for pid, st in (await get_projections(season, week)).items()}
        team_state = {}  # Sleeper team abbr -> pre/in/post
        for g in espn_games:
            for s in ("home", "away"):
                if (a := g[s].get("abbr")):
                    team_state[ESPN_TO_SLEEPER_TEAM.get(a, a)] = g.get("state")
        by_uid = {u["user_id"]: u for u in users}
        owner_of = {r["roster_id"]: by_uid.get(r.get("owner_id")) for r in rosters}

        groups: dict = {}
        for m in matchups:
            groups.setdefault(m.get("matchup_id"), []).append(m)
        pairs, watch = [], []
        for entries in groups.values():
            if len(entries) != 2:
                continue
            sides = []
            for m in entries:
                sp = m.get("starters_points") or []
                sel = [(pid, sp[i] if i < len(sp) else 0.0)
                       for i, pid in enumerate(m.get("starters") or []) if pid and pid != "0"]
                pstate = {pid: team_state.get((players.get(pid) or {}).get("team"), "pre") for pid, _ in sel}
                o = live_update.roster_outlook([p for p, _ in sel], [pt for _, pt in sel],
                                               m.get("points") or 0, proj_pts, pstate)
                team = team_name(owner_of.get(m["roster_id"]))
                sides.append({"team": team, **o})
                watch += [(pid, proj, team) for pid, proj in o["remaining"]]
            sides.sort(key=lambda s: s["projected"], reverse=True)
            pairs.append(sides)
        if not pairs:
            return
        watch.sort(key=lambda x: x[1], reverse=True)
        watch_top = [(player_line(pid, players)["name"], proj, team) for pid, proj, team in watch[:3]]
        channel = self.get_channel(cfg.results_channel_id or cfg.shame_channel_id)
        if channel is None:
            return
        await channel.send(embed=build_pulse_embed(week, final_now, total, pairs, watch_top))
        _mark_pulse(week, final_now)
        log.info("matchup pulse posted: week %d (%d/%d final)", week, final_now, total)

    @daily_nag.before_loop
    async def _before(self):
        await self.wait_until_ready()

    @results_announcer.before_loop
    async def _before_results(self):
        await self.wait_until_ready()

    @matchup_pulse.before_loop
    async def _before_pulse(self):
        await self.wait_until_ready()


if __name__ == "__main__":
    NflBot().run(cfg.discord_token)
