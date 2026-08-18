# Poverty Franchises — league bot

Standalone Discord bot for the Sleeper league **Poverty Franchises** (`1393861542625169408`).

**v1: join-nag.** Once a day it checks who hasn't joined the Sleeper league yet and
pings them in a channel, escalating the tone as the draft nears. Goes quiet once
everyone's in. First slice of what'll become the league's read layer + bot.

## How it works

```
daily @ CHECK_HOUR (local):
  joined  = Sleeper /league/{id}/users
  missing = expected − joined      # expected.toml, matched by resolved user_id
  if missing: ping them in SHAME_CHANNEL_ID, tone by days-to-draft
  else:       stay silent
```

Matching is by **Sleeper user_id** (each handle in `expected.toml` is resolved via
`/user/{username}`), so a changed display name won't cause false shame. An unresolved
handle is logged and treated as missing — that's your cue to fix the mapping.

## Setup

```sh
uv sync                       # or: pip install -e .
cp .env.example .env          # fill DISCORD_TOKEN + SHAME_CHANNEL_ID
cp expected.example.toml expected.toml   # fill all 12 invited members
python bot.py
```

Invite the bot to the server with **Send Messages** perms; it needs no privileged intents.

## Test

```sh
python test_shame.py          # or: pytest
```

## Deploy (VPS, manual — matches the other bots)

```sh
# once:
git clone <repo> /opt/nfl-bot && cd /opt/nfl-bot
python3 -m venv venv && venv/bin/pip install -e .
# create /opt/nfl-bot/.env and /opt/nfl-bot/expected.toml
sudo cp nfl-bot.service /etc/systemd/system/ && sudo systemctl enable --now nfl-bot

# updates:
cd /opt/nfl-bot && git pull && sudo systemctl restart nfl-bot
```

Config lives in `.env` (secrets) and `expected.toml` (the invited roster) — both
gitignored, so they exist only on the VPS.
