from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- LLMs ---
    anthropic_api_key: str = ""
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com/v1"

    # --- Sandbox (Phase 3) ---
    e2b_api_key: str = ""
    e2b_template: str = "base"
    e2b_timeout_seconds: int = 300

    # --- VCS ---
    github_token: str = ""

    # --- Vector store ---
    qdrant_url: str = ""
    qdrant_api_key: str = ""


settings = Settings()
