import os
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(dotenv_path=BASE_DIR / ".env", override=True)

class Settings(BaseSettings):
    DATABASE_URL: str = f"sqlite:///{BASE_DIR / 'data' / 'despachos.db'}"
    UPLOAD_DIR: str = str(BASE_DIR / "uploads")
    DATA_DIR: str = str(BASE_DIR / "data")
    OCR_ENABLED: bool = True
    OCR_LANGUAGE: str = "spa+eng+por"
    TESSERACT_CMD: str = ""
    MAX_UPLOAD_MB: int = 50
    CONFIDENCE_REVIEW: float = 0.80
    APP_TITLE: str = "Sistema de Gestión de Despachos Aduaneros"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = True

    # Turso Cloud Database
    TURSO_DATABASE_URL: str = "libsql://despachos-alifarhat.aws-us-east-1.turso.io"
    TURSO_AUTH_TOKEN: str = ""

    # Google Drive Sync
    GDRIVE_FOLDER_ID: str = "1NP6zJHL9w_bV0W1BysIDRIZ5FXZzc5Kv"
    GDRIVE_CREDENTIALS_FILE: str = "./service_account.json"

    # Backup & Updates
    BACKUP_DIR: str = str(BASE_DIR / "backups")
    UPDATES_DIR: str = str(BASE_DIR / "updates")

    # Notificaciones Telegram & Webhooks
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""
    WEBHOOK_URL: str = ""
    NOTIFICATIONS_ENABLED: bool = True

    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore"
    )

settings = Settings()

# Asegurar que existan los directorios
os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
os.makedirs(settings.DATA_DIR, exist_ok=True)
os.makedirs(settings.BACKUP_DIR, exist_ok=True)
os.makedirs(settings.UPDATES_DIR, exist_ok=True)

