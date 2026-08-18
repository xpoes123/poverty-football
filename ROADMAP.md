# Poverty Franchises — Roadmap & Working Agreement

Live site: https://nfl.djiang.xyz · Sleeper league `1393861542625169408` ("Poverty Franchises", 2026, pre-draft, draft **Sep 5 8 PM ET**).

## Working rules (autonomous agents MUST follow)
- **PR-heavy.** Every change lands on a branch `auto/<slug>` → PR against `main`. Never push to `main`. Never touch the VPS. Small commits, meaningful messages.
- **Feature-flag big features OFF.** New user-facing systems (betting, analysis) gate behind `cfg.enable_*` flags (see `config.py`), default `False`, so they can be toggled/removed. UI reads `features` in the template context.
- **Don't break the live path.** The app serves live Sleeper data, pre-draft, cached in `sleeper.py`. Keep every page working in the pre-draft (empty) state AND the in-season state.
- **No AI flavor text.** Plain, factual copy only. Never invent facts (no fake deadlines/dates/settings). See memory `no-ai-flavor-text`.
- **Verify before claiming done.** Run `python test_views.py` + `python test_shame.py`; boot the app and hit the changed route (TestClient or local uvicorn + Playwright screenshot).

## Architecture (current)
- `sleeper.py` — cached read layer. `views.py` — pure shaping (tested in `test_views.py`).
- `web.py` — FastAPI routes → Jinja templates in `templates/`, styling in `static/css/poverty.css`.
- Pages: League (home: countdown, links, standings, transactions), Draft Board (`/draftboard`, all players sortable + headshots), Free Agents (`/freeagents`), Schedule (`/schedule`), player profiles (`/player/{id}`), compare (`/compare`), team pages (`/team/{id}`).
- Discord OAuth (identify + highlight-your-team) via `expected.toml` bridge — live.
- Aesthetic: warm-espresso + antique-gold "sports almanac", Fraunces + Archivo Narrow. Keep it cohesive.

## STATUS (after overnight autonomous run, 2026-08-18)
All four requested features are BUILT and merged to main, each behind a flag (default OFF → live site unchanged).
20 PRs merged across 5 red-team rounds. To enable a feature, add the env var to `/opt/nfl-bot/.env` on the VPS and `systemctl restart nfl-web`:
- **Insights** (luck / expected-wins / all-play, `/insights`): `ENABLE_ANALYSIS=true`
- **Bet on your matchups** (`/bets`, SQLite in `data/`): `ENABLE_BETTING=true` (needs Discord login working)
- **H2H NFL-game betting** (`/h2h`, odds via OddsAPI): `ENABLE_H2H_BETTING=true` (`ODDS_API_KEY` already set)
- **Dev-seed** (fixture in-season data for off-season dev): `DEV_SEED=true` — LOCAL/dev only, never on prod.
Modules: `betting.py`+`test_betting.py`, `h2h.py`+`odds.py`+`test_h2h.py`, `views.luck_table`, `seed/` fixtures.
Deployed polish (live now): countdown, standings cutline, player profiles + compare, headshot fallbacks,
mobile-nav fix, WCAG-AA contrast, skip-link, focus rings, signed credit/debit colors, table-wrap scroll.

## Requested features (build as feature-flagged PRs)
1. **Bet on your own matchups** (`enable_betting`) — each week, log a wager on your own game; track outcomes/standings of bets. Play-money ledger; needs a small store (SQLite) + logged-in identity.
2. **H2H NFL game betting** (`enable_h2h_betting`) — members bet against each other on that week's NFL games. Odds from the-odds-api.com (`cfg.odds_api_key`); a handshake/escrow ledger to track who owes whom. Cache odds; do NOT hammer the API.
3. **Deeper analysis** (`enable_analysis`) — luck/expected-wins (all-play record), power rankings, points distribution, "were you unlucky this week". Pure computation on Sleeper data.
4. **Dev-seed build** (`dev_seed`) — serve seeded fixtures (drafted rosters, weekly matchups, stats) so the season UI can be developed off-season. A `seed/` fixture set + a switch in `sleeper.py` that reads fixtures instead of the network when `cfg.dev_seed`.

## Red-team focus (every round)
- **Design critic**: cohesion, hierarchy, spacing, typography, color, alignment, mobile. Flag anything that reads as generic/AI or inconsistent with the almanac aesthetic.
- **UX critic**: navigation clarity, empty states, discoverability, tap targets, load feel, accessibility (contrast, labels, keyboard).
- **Football/product critic**: is this useful to a 12-person league? What would a degenerate fantasy manager actually want to see? What's missing, what's noise?

## Accumulated feedback (honor it)
- No flavor text / invented facts. Team-name-only in standings. Default crest for teams w/o a picture. Login on far right. Player rows click to profiles. Compare via + search. Draft countdown by the second. Reorganized profile (headline strip + stat rows). Fixed ledger right padding.
