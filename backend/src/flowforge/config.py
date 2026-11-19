from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FLOWFORGE_", extra="ignore")
    database_url: str
    rabbitmq_url: str
    telemetry_enabled: bool = False
    otlp_traces_endpoint: str = "http://jaeger:4318/v1/traces"
