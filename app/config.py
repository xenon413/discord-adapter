from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)

    discord_bot_token: SecretStr
    api_secret_key: SecretStr
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    shutdown_timeout: float = Field(default=15, gt=0, le=120)
    message_cache_size: int = Field(default=1000, ge=1, le=100000)
    members_intent: bool = True
    message_content_intent: bool = True
    event_queue_size: int = Field(default=1000, ge=1, le=100000)
    subscriber_queue_size: int = Field(default=100, ge=1, le=10000)
    max_subscribers: int = Field(default=32, ge=1, le=1000)
    webhook_timeout: float = Field(default=5, gt=0, le=60)
    webhook_attempts: int = Field(default=3, ge=1, le=10)
    webhook_retry_delay: float = Field(default=0.5, ge=0, le=10)
    voice_enabled: bool = False
    voice_connect_timeout: float = Field(default=20, gt=0, le=60)
    max_voice_sessions: int = Field(default=8, ge=1, le=100)
    voice_max_users: int = Field(default=16, ge=1, le=100)
    voice_buffer_bytes: int = Field(default=384000, ge=3840, le=1920000, multiple_of=4)

    @field_validator("discord_bot_token", "api_secret_key")
    @classmethod
    def validate_secret(cls, value: SecretStr) -> SecretStr:
        secret = value.get_secret_value()
        if not secret.strip():
            raise ValueError("Secret must not be empty")
        return value

    @field_validator("api_secret_key")
    @classmethod
    def validate_api_key(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value()) < 16:
            raise ValueError("API secret must contain at least 16 characters")
        return value
