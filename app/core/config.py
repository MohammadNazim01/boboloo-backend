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
    # MQTT — EMQX Cloud
    # ===============================
    MQTT_HOST: str = "broker.hivemq.com"
    MQTT_PORT: int = 8883
    MQTT_USERNAME: str | None = None    # Gateway service-account username
    MQTT_PASSWORD: str | None = None    # Gateway service-account password
    MQTT_USE_TLS: bool = True
    MQTT_GATEWAY_CLIENT_ID: str = "boboloo-gateway"

    # Secret that EMQX sends in X-Mqtt-Auth-Secret header when calling our
    # auth/ACL endpoints. Set the same value in EMQX HTTP auth plugin config.
    MQTT_AUTH_SECRET: str = ""

    # ===============================
    # S3 — Firmware storage
    # ===============================
    AWS_REGION: str = "us-east-1"
    # Explicit credentials for local dev / Fly.io.
    # On AWS ECS/EC2, leave these unset and use the instance IAM role instead.
    AWS_ACCESS_KEY_ID: str | None = None
    AWS_SECRET_ACCESS_KEY: str | None = None
    S3_FIRMWARE_BUCKET: str = ""
    # TTL for pre-signed download URLs sent to toys (seconds).
    S3_PRESIGN_EXPIRY: int = 1800  # 30 minutes

    # ===============================
    # SENTRY
    # ===============================
    SENTRY_DSN: str | None = None

    # ===============================
    # CORS
    # ===============================
    # Comma-separated list of allowed origins, e.g.:
    # "https://app.boboloo.com,https://admin.boboloo.com"
    # Use "*" only for development.
    CORS_ORIGINS: str = "*"

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