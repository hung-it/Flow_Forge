from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FLOWFORGE_", extra="ignore")
    database_url: str
    rabbitmq_url: str
