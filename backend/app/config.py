"""Application settings. Secrets come from environment variables or the git-ignored backend/.env."""
from functools import lru_cache
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent
DEV_JWT_SECRET = "dev-only-insecure-secret-change-me-in-env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", extra="ignore")

    env: str = "development"
    database_url: str = f"sqlite:///{(BASE_DIR / 'jee_dev.db').as_posix()}"
    jwt_secret: str = DEV_JWT_SECRET
    jwt_expiry_minutes: int = 60 * 24 * 7
    cookie_secure: bool = False
    # Official papers, cropped question images and model outputs. Never committed: paper text is copyrighted.
    live_dir: Path = BASE_DIR / "live"
    # Behind the Cloudflare tunnel every request comes from 127.0.0.1; the visitor's address is in CF-Connecting-IP.
    # Only switch this on when a tunnel or proxy you control is the only way in (serve_public.py does).
    trust_proxy_headers: bool = False
    rate_limit_default: int = 300
    rate_limit_auth: int = 20

    # Free models only (paid question generation is off by design).
    openrouter_api_key: str = ""
    # Vision readers, tried in order until two have answered; a reading is kept only when both agree.
    transcribe_models: list[str] = ["nvidia/nemotron-3.5-super-vl:free",
                                    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free", "google/gemma-4-31b-it:free"]
    variant_writer_model: str = "nvidia/nemotron-3-ultra-550b-a55b:free"
    variant_checker_models: list[str] = ["nvidia/nemotron-3.5-super-vl:free",
                                         "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free", "google/gemma-4-31b-it:free"]
    # AI assistant: free tool-calling models, tried in order.
    assistant_models: list[str] = ["nvidia/nemotron-3-ultra-550b-a55b:free", "google/gemma-4-31b-it:free",
                                   "nvidia/nemotron-3.5-super-vl:free"]
    local_llm_url: str = "http://localhost:1234/v1/chat/completions"  # LM Studio; optional
    # The free pool (1000 calls a day) is shared with NEET Compass's jobs and this site's assistant: background jobs
    # (transcription, mock questions) stop after this many calls a day so students' questions still get answers.
    background_daily_calls: int = 250
    unseen_buffer: int = 40  # top up mock variants when a student has fewer unseen than this per subject
    # Shared admin console: the NEET site's address, and the secret both sites use for the NEET | JEE switcher.
    neet_url: str = "http://localhost:8765"
    admin_bridge_secret: str = ""

    # Links in emails (guardian consent). LINK_BASE overrides PUBLIC_APP_URL when the site's own address changes
    # (a Cloudflare quick tunnel): the fixed GitHub Pages link forwards the part after '#'.
    public_app_url: str = "http://localhost:8790"
    link_base: str = ""
    # Email (guardian consent links). "console" saves the email under live/outbox instead of sending it.
    email_backend: str = "console"
    email_from: str = "no-reply@example.invalid"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_starttls: bool = True
    privacy_notice_version: str = "2026-11-25"
    contact_email: str = ""
    guardian_link_hours: int = 72
    unconsented_minor_retention_days: int = 7
    # Google sign-in (OAuth client from Google Cloud Console → APIs & Services → Credentials).
    google_client_id: str = ""
    google_client_secret: str = ""
    # Empty: <PUBLIC_APP_URL>/auth/google/callback. The live site sets it to the fixed link's google-callback.html.
    google_redirect_uri: str = ""
    # Phone sign-in with an SMS code, through Tekurious AI-Auth (server-side only; never sent to browsers).
    tekurious_api_key: str = ""
    tekurious_base_url: str = "https://api.tekurious.si"
    otp_resend_seconds: int = 45
    # The service's test ("stub") mode accepts one fixed code for every number; refuse it unless switched on.
    phone_allow_stub: bool = False

    @property
    def google_enabled(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret)

    @property
    def phone_enabled(self) -> bool:
        return bool(self.tekurious_api_key)

    def link(self, path: str) -> str:
        """A link to this site for emails, that keeps working when the site's address changes."""
        return (self.link_base or self.public_app_url).rstrip("/") + path

    @model_validator(mode="after")
    def production_guards(self):
        if self.env != "development" and self.jwt_secret == DEV_JWT_SECRET:
            raise ValueError("Refusing to start: set JWT_SECRET (32+ random characters) outside development")
        if self.env == "production":
            problems = []
            if self.jwt_secret == DEV_JWT_SECRET or len(self.jwt_secret) < 32:
                problems.append("JWT_SECRET must be 32+ random characters")
            if not self.cookie_secure:
                problems.append("COOKIE_SECURE must be true")
            if self.email_backend != "smtp":
                problems.append("EMAIL_BACKEND must be smtp (guardian consent emails)")
            if problems:
                raise ValueError("Refusing to start in production: " + "; ".join(problems))
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
