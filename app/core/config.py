from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "FulfillCore MVP"
    database_url: str = "postgresql+psycopg://fulfillcore:fulfillcore@localhost:5432/fulfillcore"
    app_secret_key: str = "fulfillcore-local-dev-key-change-before-production"

    admin_username: str = "admin"
    admin_password_hash: str = ""
    session_ttl_hours: int = 12
    secure_cookie: bool = False
    print_agent_token: str = ""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
