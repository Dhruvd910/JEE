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
    local_llm_url: str = "http://localhost:1234/v1/chat/completions"  # LM Studio; optional
    unseen_buffer: int = 40  # top up mock variants when a student has fewer unseen than this per subject
    # Shared admin console: the NEET site's address, and the secret both sites use for the NEET | JEE switcher.
    neet_url: str = "http://localhost:8765"
    admin_bridge_secret: str = ""

    @model_validator(mode="after")
    def production_guards(self):
        if self.env == "production":
            problems = []
            if self.jwt_secret == DEV_JWT_SECRET or len(self.jwt_secret) < 32:
                problems.append("JWT_SECRET must be 32+ random characters")
            if not self.cookie_secure:
                problems.append("COOKIE_SECURE must be true")
            if problems:
                raise ValueError("Refusing to start in production: " + "; ".join(problems))
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
