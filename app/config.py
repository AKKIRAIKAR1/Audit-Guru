from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')
    database_url: str = 'sqlite:///./data/ocr.db'
    storage_backend: str = 'local'
    local_storage_path: str = './data/objects'
    api_keys: dict[str, str] = {}
    admin_username: str = 'Admin'
    admin_password_hash: str = ''
    admin_organization: str = 'demo-organization'
    session_hours: int = Field(8, ge=1, le=168)
    session_cookie_secure: bool = False
    signing_secret: str = 'replace-with-a-random-secret-at-least-32-characters'
    redis_url: str = 'redis://localhost:6379/0'
    ocr_provider: str = 'tesseract'
    ocr_fallback: str = 'tesseract'
    ocr_language: str = 'eng'
    paddle_language: str = 'en'
    max_file_mb: int = Field(10, ge=1, le=100)
    max_pages: int = Field(50, ge=1, le=500)
    max_image_pixels: int = Field(25_000_000, ge=1000)
    ocr_timeout_seconds: int = Field(90, ge=1)
    job_lease_seconds: int = Field(900, ge=10)
    max_attempts: int = Field(3, ge=1)
    quality_confidence_threshold: float = Field(.7, ge=0, le=1)
    blur_threshold: float = 60
    s3_endpoint_url: str | None = None
    s3_access_key: str = ''
    s3_secret_key: str = ''
    s3_bucket: str = 'invoices'
    s3_region: str = 'us-east-1'
    s3_server_side_encryption: str = ''
    clamav_host: str = ''
    clamav_port: int = 3310


@lru_cache
def settings():
    return Settings()
