from functools import lru_cache
from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

try:
    from dotenv import load_dotenv
    BASE_DIR = Path(__file__).resolve().parent
    ENV_PATH = BASE_DIR / ".env"
    load_dotenv(dotenv_path=ENV_PATH, override=True)
    print(f"Loaded .env from: {ENV_PATH} (exists: {ENV_PATH.exists()})")
except ImportError:
    pass


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    app_name: str = Field(default="Jarvis 2.0", alias="APP_NAME")
    environment: str = Field(default="development", alias="ENVIRONMENT")
    cors_origins: str = Field(
        default="http://localhost:5173,http://127.0.0.1:5173",
        alias="CORS_ORIGINS",
    )

    # Gemini
    gemini_api_key: str | None = Field(default=None, alias="GEMINI_API_KEY")
    gemini_model: str = Field(default="gemini-2.5-flash", alias="GEMINI_MODEL")


    # Turn-based voice transcription.
    gemini_stt_model: str = Field(
        default="gemini-2.5-flash", alias="GEMINI_STT_MODEL"
    )
    gemini_stt_timeout_seconds: float = Field(
        default=60.0, ge=5.0, le=180.0, alias="GEMINI_STT_TIMEOUT_SECONDS"
    )
    voice_max_audio_bytes: int = Field(
        default=6_000_000, ge=100_000, le=20_000_000, alias="VOICE_MAX_AUDIO_BYTES"
    )

    serper_api_key: str = ""
    # Perplexity
    perplexity_api_key: str | None = Field(default=None, alias="PERPLEXITY_API_KEY")

    # Redis / Session Memory
    redis_url: str = Field(default="redis://localhost:6379", alias="REDIS_URL")
    session_ttl_seconds: int = Field(default=86400, alias="SESSION_TTL_SECONDS")  # 24h

    # Local app security. If JARVIS_APP_TOKEN is set, protected routes require it.
    jarvis_app_token: str | None = Field(default=None, alias="JARVIS_APP_TOKEN")
    jarvis_auth_required: bool = Field(default=False, alias="JARVIS_AUTH_REQUIRED")

    # Feature flags for high-risk or optional tool surfaces.
    enable_self_modify: bool = Field(default=False, alias="ENABLE_SELF_MODIFY")
    enable_mcp_filesystem: bool = Field(default=True, alias="ENABLE_MCP_FILESYSTEM")
    enable_code_execution: bool = Field(default=True, alias="ENABLE_CODE_EXECUTION")
    enable_web_scraper: bool = Field(default=True, alias="ENABLE_WEB_SCRAPER")

    # File and URL hardening.
    upload_max_bytes: int = Field(default=25_000_000, alias="UPLOAD_MAX_BYTES")
    upload_allowed_extensions: str = Field(
        default=".csv,.json,.txt,.xlsx,.png,.jpg,.jpeg,.webp",
        alias="UPLOAD_ALLOWED_EXTENSIONS",
    )
    scraper_block_private_networks: bool = Field(
        default=True,
        alias="SCRAPER_BLOCK_PRIVATE_NETWORKS",
    )

    openweather_api_key: str = ""

    newsdata_api_key: str = ""

    newsdata_url: str = (
        "https://newsdata.io/api/1/latest"
    )
    e2b_api_key: str = ""

    # Music / YouTube
    youtube_api_key: str | None = Field(default=None, alias="YOUTUBE_API_KEY")
    youtube_region_code: str = Field(default="IN", alias="YOUTUBE_REGION_CODE")
    youtube_relevance_language: str = Field(
        default="en", alias="YOUTUBE_RELEVANCE_LANGUAGE"
    )
    youtube_search_limit: int = Field(default=8, alias="YOUTUBE_SEARCH_LIMIT")
    youtube_timeout_seconds: float = Field(
        default=12.0, alias="YOUTUBE_TIMEOUT_SECONDS"
    )
    youtube_cache_ttl_seconds: int = Field(
        default=600, alias="YOUTUBE_CACHE_TTL_SECONDS"
    )

    #F13 Google Auth. and encryption 
    google_client_id: str =""
    google_client_secret: str=""
    google_redirect_uri:str="http://localhost:8000/api/auth/google/callback"
    token_encryption_key: str =""

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def upload_extension_set(self) -> set[str]:
        return {
            value.strip().lower()
            for value in self.upload_allowed_extensions.split(",")
            if value.strip()
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
