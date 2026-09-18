from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Defaults to a local sqlite file so you can run this with zero setup.
    # Point this at Postgres once you're ready: postgresql+psycopg2://user:pass@host:5432/dbname
    database_url: str = "sqlite:///./dev.db"

    jwt_secret: str = "dev-secret-change-me"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24  # 1 day

    # Same values as your rag.py — carried over so retrieval behaves the same.
    embed_model: str = "all-MiniLM-L6-v2"
    chunk_words: int = 600
    chunk_overlap: int = 50
    top_k: int = 6
    web_results: int = 4
    chroma_dir: str = "./chroma_db"

    # ── LLM provider ──────────────────────────────────────────────────────
    # "groq" (hosted, free tier, no server to run) or "ollama" (local, needs
    # real RAM -- fine for your own machine, not for a small free host).
    llm_provider: str = "groq"
    groq_api_key: str = ""
    default_llm_model: str = "llama-3.1-8b-instant"

    # ── File storage ──────────────────────────────────────────────────────
    # "local" writes to disk on whatever machine runs the server.
    # "s3" works with any S3-compatible bucket (Cloudflare R2, Backblaze B2,
    # AWS S3, or a self-hosted MinIO) -- same code either way.
    storage_backend: str = "local"
    storage_dir: str = "./storage"
    s3_bucket: str = ""
    s3_endpoint_url: str = ""  # e.g. https://<account_id>.r2.cloudflarestorage.com
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_region: str = "auto"

    # ── Account limits ────────────────────────────────────────────────────
    max_users: int = 5
    max_notebooks_per_user: int = 1
    max_storage_bytes_per_user: int = 400 * 1024 * 1024  # ~400MB (2GB / 5 users)

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()
