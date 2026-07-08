"""Application configuration.

Defines the `Settings` model (env-file/environment-backed) used throughout
the app, and a cached accessor `get_settings` for retrieving it.
"""

import os
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
      """Application settings, populated from environment variables and/or a `.env` file.

      Covers app metadata, database location, ElevenLabs outbound voice
      integration, SurveyMonkey API/OAuth/webhook settings, and Redis/RQ
      job queue configuration. See `model_config` for the env file source.
      """

      app_name: str = "Candidate Experience API"
      app_version: str = "0.1.0"
      env: str = "development"
      job_timezone: str = "Africa/Lagos"
      database_url: str = "sqlite:///./candidate_experience.db"
      handoff_phone_number: str = "+2348100000000"
      handoff_hold_message: str = "Please hold while we connect you to an officer."             
      public_base_url: str = "http://localhost:8000"
      elevenlabs_webhook_secret: str | None = None
      elevenlabs_api_key: str | None = None
      elevenlabs_outbound_agent_id: str | None = None
      elevenlabs_outbound_phone_number_id: str | None = None
      elevenlabs_outbound_concurrency_limit: int | None = None
      surveymonkey_base_url: str = "https://api.surveymonkey.com/v3"
      surveymonkey_access_token: str | None = None

      surveymonkey_campaign_survey_id: str | None = None
      surveymonkey_campaign_collector_id: str | None = None
      surveymonkey_csat_survey_id: str | None = None
      surveymonkey_csat_collector_id: str | None = None

      surveymonkey_client_id: str | None = None
      surveymonkey_client_secret: str | None = None
      surveymonkey_redirect_uri: str | None = None
      surveymonkey_webhook_secret: str | None = None

      redis_url: str = "redis://localhost:6379/0"
      rq_default_queue: str = "candidate-experience"

      model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8",)


@lru_cache()
def get_settings() -> Settings:
    """Return the process-wide `Settings` instance.

    Cached with `lru_cache` so the environment/`.env` file is only read
    once per process and the same `Settings` object is reused everywhere.
    """
    return Settings()
