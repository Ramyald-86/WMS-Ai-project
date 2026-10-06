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

    # Multi-fulfillment host routing. Example: PLATFORM_BASE_DOMAIN=wms.example.ru
    # then fc-spb.wms.example.ru resolves fulfillment.subdomain == "fc-spb".
    platform_base_domain: str = ""
    # Extra apex hosts (comma-separated) treated as platform, not tenant subdomains.
    platform_host_aliases: str = ""
    # off | soft | strict — see app.services.tenant_host
    tenant_host_mode: str = "soft"
    # Comma-separated browser origins for ZIPLINE HTML consoles (credentials cookies).
    cors_origins: str = (
        "http://127.0.0.1:5500,http://localhost:5500,"
        "http://127.0.0.1:8000,http://localhost:8000,"
        "http://127.0.0.1:4173,http://localhost:4173,"
        "null"
    )

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
