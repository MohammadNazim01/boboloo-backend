from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):

    # ===============================
    # DATABASE
    # ===============================
    DATABASE_URL: str

    # ===============================
    # OPENAI
    # ===============================
    OPENAI_API_KEY: str

    # ===============================
    # REDIS 
    # ===============================
    REDIS_URL: str

    # ===============================
    # FIREBASE
    # ===============================
    FIREBASE_CREDENTIALS_PATH: str | None = None

    # ===============================
    # SECURITY KEYS
    # ===============================
    FACTORY_SECRET_KEY: str
    INTERNAL_CRON_SECRET: str
    ADMIN_INTERNAL_SECRET: str

    # ===============================
    # ENV
    # ===============================
    ENVIRONMENT: str = "development"

    class Config:
        env_file = ".env"
        extra = "ignore"
        case_sensitive = True


@lru_cache
def get_settings():
    return Settings()


settings = get_settings()