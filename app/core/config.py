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

      surveymonkey_campaign_template_survey_id: str | None = None
      surveymonkey_csat_template_survey_id: str | None = None
      surveymonkey_campaign_survey_id: str | None = None
      surveymonkey_campaign_collector_id: str | None = None
      surveymonkey_csat_survey_id: str | None = None
      surveymonkey_csat_collector_id: str | None = None

      # Safety flag: False = CSAT invitations are only created on demand
      # (manual API / job). True = the inbound voice webhook auto-creates a
      # CSAT invitation for every eligible finished call. Kept off until the
      # standing survey/collector are set and the team is ready to send, so
      # turning CSAT "live" is one flag, not a code change.
      csat_auto_create: bool = False

      surveymonkey_client_id: str | None = None
      surveymonkey_client_secret: str | None = None
      surveymonkey_redirect_uri: str | None = None
      surveymonkey_webhook_secret: str | None = None

      zoho_accounts_base_url: str = "https://accounts.zoho.com"
      zoho_desk_base_url: str = "https://desk.zoho.com/api/v1"
      zoho_client_id: str | None = None
      zoho_client_secret: str | None = None
      zoho_refresh_token: str | None = None
      zoho_org_id: str | None = None
      zoho_department_id: str | None = None

      inference_base_url: str | None = None
      inference_api_key: str | None = None

      kb_dir: str = "kb"
      chroma_dir: str = "chroma_data"
      # Cosine-distance ceiling for "the KB actually covers this question".
      # Calibrated July 2026: real hits scored <=0.32, out-of-scope >=0.46.
      kb_grounding_threshold: float = 0.40

      # Address drafts are written from (discovered from real outbound threads).
      helpdesk_from_email: str = "invitation@dragnet-solutions.com"
      # Safety flag: False = generate drafts into our audit log only.
      # True = also place them on Zoho tickets (officers will see them).
      helpdesk_draft_execute: bool = False

      # Safety flag: False = record tags/routing decisions in our audit log
      # only. True = write tags (and any mapped assignment) onto the real
      # Zoho ticket. Separate from helpdesk_draft_execute — tagging is lower
      # risk (no candidate-facing content) but still touches live tickets,
      # so it stays gated until officers are briefed, same as drafts.
      helpdesk_tag_execute: bool = False

      # Safety flag: False = WhatsApp answers are generated and recorded in
      # the audit log only, never sent. True = send them immediately as a
      # real WhatsApp reply — SEPARATE from helpdesk_draft_execute and
      # deliberately more cautious: a draft still waits for an officer to
      # review and click send, but a WhatsApp answer with this flag on goes
      # straight to the candidate with no human in the loop. Also: the
      # Zoho API this uses (see ZohoDeskClient.send_whatsapp_reply) is
      # unverified against a live WhatsApp ticket — do not enable until
      # that's confirmed.
      helpdesk_whatsapp_auto_reply_execute: bool = False

      # Safety flag: False (default) = grounded email answers are drafted per
      # helpdesk_draft_execute, same as always — an officer reviews and sends.
      # True = skip the draft step entirely and send the reply immediately via
      # ZohoDeskClient.send_reply — SEPARATE from helpdesk_draft_execute, and
      # a strictly bigger trust step: nobody reads the email before it reaches
      # the candidate. Takes priority over helpdesk_draft_execute when both
      # are on (auto-send is the more deliberate choice of the two). Unlike
      # create_draft_reply (verified live in August 2026), send_reply has
      # never been called against real Zoho — confirm it works against a real
      # test ticket before relying on it for real candidate traffic.
      helpdesk_email_auto_reply_execute: bool = False

      # Cron expression for how often the pipeline scheduler enqueues
      # run_helpdesk_pipeline_job. Default: every 5 minutes.
      helpdesk_pipeline_cron: str = "*/5 * * * *"

      # Cron expression for how often the campaign orchestration scheduler
      # enqueues run_campaign_orchestration_job (advances each active campaign
      # through its lifecycle). Default: every 1 minute.
      #
      # This was */10 (every 10 minutes) until the 2026-09-15 capacity review:
      # with a 2-3 min average call, a slot that frees up 30 seconds after a
      # tick sat idle for up to ~9.5 more minutes before the next tick could
      # refill it — the 10-minute cadence, not concurrency, was the real
      # throughput ceiling for short calls. A 1-minute tick keeps slots
      # refilled close to as soon as they free up.
      campaign_orchestration_cron: str = "* * * * *"
      # Max outbound calls the orchestrator places per campaign per tick.
      #
      # Raised from 1 to 6 on 2026-09-15. Our ElevenLabs plan (Creator) caps
      # concurrent calls at 10, SHARED between inbound and outbound — 6
      # leaves 4 lines free for real candidates calling in while an outbound
      # campaign runs. For a dedicated low-inbound push (e.g. a Saturday),
      # this can be raised further (temporarily, per-run) since less inbound
      # traffic is expected to compete for the pool that day.
      campaign_outbound_concurrency: int = 6
      # Max outbound call attempts per candidate before the orchestrator gives
      # up and completes the campaign. The orchestrator auto-requeues retryable
      # outcomes (no_answer/busy/voicemail/failed) up to this cap. Set to 1 to
      # disable auto-retry (one attempt only).
      campaign_outbound_max_attempts: int = 3

      zoho_webhook_token: str | None = None

      # Shared secret ElevenLabs sends (Authorization: Bearer ...) when calling the
      # KB retrieval tool. Leave unset in dev; MUST be set wherever the endpoint is
      # publicly reachable, or anyone with the URL can query the KB + burn embed cost.
      voice_kb_tool_token: str | None = None

      # OpenAI-compatible proxy to the inference gateway. ElevenLabs can't reach the
      # gateway directly (it's behind Cloudflare, which blocks its server calls), so
      # ElevenLabs points its Custom LLM at OUR /llm/v1 route (reachable via ngrok)
      # and we forward server-side to the gateway — the same call that works in curl.
      llm_gateway_chat_url: str = "https://gpu.idhub.ng/v1/chat/completions"
      llm_gateway_key: str | None = None   # OpenAI-compat key the gateway expects
      llm_proxy_token: str | None = None    # token ElevenLabs sends us (keeps the proxy closed)


      # Azure AD app registration for reading the Helpdesk KB from SharePoint
      # (Microsoft Graph, client-credentials flow, Sites.Selected permission).
      kb_reader_tenant_id: str | None = None
      kb_reader_client_id: str | None = None
      # Azure's id for the secret itself — not used for auth, just kept for
      # reference when it's time to rotate/expire the secret in Azure AD.
      kb_reader_secret_id: str | None = None
      kb_reader_secret_value: str | None = None
      sharepoint_hostname: str = "dragnetnigeria.sharepoint.com"
      sharepoint_site_path: str = "/sites/everybody"
      # Name of the document library the KB docs live in, e.g. "Candidate
      # experience KB". None means "use the site's default library" — most
      # sites only have one, but this site's KB lives in its own, so this
      # must be set for it to resolve to the right library, not an empty one.
      sharepoint_library_name: str | None = None


      redis_url: str = "redis://localhost:6379/0"
      rq_default_queue: str = "candidate-experience"

      # Browser origins allowed to call this API (CORS). Comma-separated.
      # The frontend scaffold's Vite dev server runs on :5173. Add the
      # deployed UI's origin(s) here in production.
      cors_allow_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

      model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8",)

      @property
      def cors_allow_origins_list(self) -> list[str]:
          """Parse the comma-separated CORS origins into a clean list."""
          return [origin.strip() for origin in self.cors_allow_origins.split(",") if origin.strip()]


@lru_cache()
def get_settings() -> Settings:
    """Return the process-wide `Settings` instance.

    Cached with `lru_cache` so the environment/`.env` file is only read
    once per process and the same `Settings` object is reused everywhere.
    """
    return Settings()
