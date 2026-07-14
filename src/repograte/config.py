from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # LLMs
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5"
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com/v1"

    # Sandbox
    e2b_api_key: str = ""
    e2b_template: str = "base"
    e2b_timeout_seconds: int = 300
    # Verification commands run inside the sandbox. Override per-project via env vars
    # (or per-run with `--install-cmd` / `--test-cmd`) for non-TypeScript repos, e.g.:
    #   SANDBOX_TEST_CMD="npm run lint"
    sandbox_install_cmd: str = "npm install --no-audit --no-fund"
    sandbox_test_cmd: str = "npx tsc --noEmit"

    # VCS
    github_token: str = ""

    # Vector store
    qdrant_url: str = ""
    qdrant_api_key: str = ""
    # Best-effort retrieval of sibling components in the target repo, surfaced to the
    # Architect as extra context. Requires cloning the repo and downloading a small
    # embedding model on first use; set to False to skip entirely (e.g. offline/CI use).
    enable_rag_context: bool = True
    rag_max_files: int = 40

    # Orchestration
    max_correction_loops: int = 4
    # Path to a SQLite file for checkpoint persistence
    # Set to "" to use a pure in-memory checkpointer instead (state is lost when the process exits).
    checkpoint_path: str = ".repograte/checkpoints.sqlite"

    # Codebase-wide runs
    default_migration: str = "react-class-to-hooks"
    # Hard cap on how many files a single codebase-wide run will touch, independent
    # of rag_max_files (which only bounds how many sibling files get scanned for
    # context, not how many get migrated). Keeps a mistaken --repo-url pointed at a
    # huge monorepo from turning into an unbounded number of LLM/sandbox calls.
    codebase_max_files: int = 100


settings = Settings()
