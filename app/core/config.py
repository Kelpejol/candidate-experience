import os
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
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

      model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8",)


@lru_cache()
def get_settings() -> Settings:
    return Settings()