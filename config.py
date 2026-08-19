from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    pg_host: str
    pg_port: int = 5432
    pg_db: str
    pg_user: str
    pg_password: str

    active_db: str = "postgresql"

    mqtt_broker: str = "127.0.0.1"
    mqtt_port: int = 1883
    mqtt_client_id: str = "fastapi-server"
    mqtt_username: str
    mqtt_password: str

    api_title: str = "Fabrika IoT API"
    api_version: str = "1.0.0"
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    auth_mode: str = "audit"
    auth_secret: str
    cors_origins: str

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
