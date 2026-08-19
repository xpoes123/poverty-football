from datetime import date

from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    discord_token: str
    shame_channel_id: int
    bet_channel_id: int | None = None  # where proposed h2h bets post for claiming; None = don't post
    results_channel_id: int | None = None  # weekly fantasy-results announcer; None = use shame channel
    league_id: str = "1393861542625169408"  # Poverty Franchises
    draft_date: date | None = date(2026, 9, 5)  # escalation ramps as this nears; env-overridable
    draft_time_label: str = "8 PM ET"  # shown next to the draft date
    join_url: str = "https://sleeper.com/i/LVlN2Jaz9Owb3"
    check_hour: int = 20  # local hour (see timezone) for the daily nag → 8 PM ET
    nag_start_date: date | None = date(2026, 8, 19)  # don't nag before this day
    timezone: str = "America/New_York"

    # Discord OAuth (portal only). Empty → login is simply hidden.
    discord_client_id: str = ""
    discord_client_secret: str = ""
    session_secret: str = "dev-insecure-change-me"
    oauth_redirect: str = "https://nfl.djiang.xyz/auth/callback"

    # Feature flags — big features ship OFF; flip per-env to enable/remove cleanly.
    enable_betting: bool = False      # bet on your own matchups
    enable_h2h_betting: bool = False  # bet against each other on NFL games (needs odds_api_key)
    enable_analysis: bool = False     # deeper stats / luck analysis
    odds_api_key: str = ""            # the-odds-api.com
    dev_seed: bool = False            # serve seeded fixtures instead of live Sleeper (off-season dev)

    @property
    def oauth_enabled(self) -> bool:
        return bool(self.discord_client_id and self.discord_client_secret)


cfg = Config()
