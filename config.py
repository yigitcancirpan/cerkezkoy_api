from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    mqtt_broker: str = "localhost"
    mqtt_port: int = 1883
    mqtt_client_id: str = "fastapi-server"
    mqtt_username: Optional[str] = None
    mqtt_password: Optional[str] = None

    pg_host: str = "localhost"
    pg_port: int = 5432
    pg_db: str = "cerkezkoy_db"
    pg_user: str = "yigitcanc"
    pg_password: str = "***REMOVED***"

    # mssql_host: str = "192.168.1.200"
    # mssql_port: int = 1433
    # mssql_db: str = "FabrikaIoT"
    # mssql_user: str = "iot_user"
    # mssql_password: str = ""

    active_db: str = "postgresql"

    api_title: str = "Fabrika IoT API"
    api_version: str = "1.0.0"
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"   # DB_URL, SITE_CODE gibi servis-ortak değişkenleri yok say


settings = Settings()