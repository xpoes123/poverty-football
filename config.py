from datetime import date

from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    discord_token: str
    shame_channel_id: int
    league_id: str = "1393861542625169408"  # Poverty Franchises
    draft_date: date | None = None  # escalation ramps as this nears; None = mild tone
    draft_time_label: str = "8 PM ET"  # shown next to the draft date
    join_url: str = "https://sleeper.com/i/LVlN2Jaz9Owb3"
    check_hour: int = 18  # local hour (see timezone) for the daily nag
    timezone: str = "America/New_York"


cfg = Config()
