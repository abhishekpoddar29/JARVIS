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
    serper_api_key: str = ""
    # Perplexity
    perplexity_api_key: str | None = Field(default=None, alias="PERPLEXITY_API_KEY")

    # Redis / Session Memory
    redis_url: str = Field(default="redis://localhost:6379", alias="REDIS_URL")
    session_ttl_seconds: int = Field(default=86400, alias="SESSION_TTL_SECONDS")  # 24h

    openweather_api_key: str = ""

    newsdata_api_key: str = ""

    newsdata_url: str = (
        "https://newsdata.io/api/1/latest"
    )
    e2b_api_key: str = ""

    #F13 Google Auth. and encryption 
    google_client_id: str =""
    google_client_secret: str=""
    google_redirect_uri:str="http://localhost:8000/api/auth/google/callback"
    token_encryption_key: str =""

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()