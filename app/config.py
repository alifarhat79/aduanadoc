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
    APP_VERSION: str = "1.3.1"
    DEBUG: bool = True

    # Turso Cloud Database
    TURSO_DATABASE_URL: str = "libsql://despachos-alifarhat.aws-us-east-1.turso.io"
    TURSO_AUTH_TOKEN: str = "eyJhbGciOiJFZERTQSIsInR5cCI6IkpXVCJ9.eyJhIjoicnciLCJpYXQiOjE3ODc3Njk1NzcsImlkIjoiMDFhMDNmNTItOGQwMS03YmMzLTk0YmItYjZjZTA5YzU2ZjM2Iiwia2lkIjoiSE9nOF8zQjZsR2k4LWF0YUQ3NDlzSzJob25SV3NuTVpYbTF3TEVpREtnMCIsInJpZCI6IjVlNjVmZTljLTY2ZmUtNGE4MS1hNTIxLTUyMWZjMDk5ZWM0ZSJ9.jZMp4XqDHo-dCXNZkHSA9d3BfyYgM4WVk4lgsxlmPP4ng93uf0pA2C5HNoDw7JiIFRIV5tsL6CEVFUPWaAgJBg"

    # Google Drive Sync
    GDRIVE_FOLDER_ID: str = "1NP6zJHL9w_bV0W1BysIDRIZ5FXZzc5Kv"
    GDRIVE_CREDENTIALS_FILE: str = "./service_account.json"
    GDRIVE_WATCHER_ENABLED: bool = True
    GDRIVE_WATCHER_INTERVAL: int = 60

    # Backup & Updates
    BACKUP_DIR: str = str(BASE_DIR / "backups")
    UPDATES_DIR: str = str(BASE_DIR / "updates")
    BACKUP_MAX_KEEP: int = 3

    # Notificaciones Telegram & Webhooks
    TELEGRAM_BOT_TOKEN: str = "8815110549:AAGsv9YjEqrYAqRS-qsCJh1U5prxVvIi2bI"
    TELEGRAM_CHAT_ID: str = "67567393,817311653"
    WEBHOOK_URL: str = ""
    NOTIFICATIONS_ENABLED: bool = True

    # Seguridad / Acceso Exclusivo Programador
    CONFIG_ADMIN_PASSWORD: str = "Sohalia2012*@"
    SECRET_KEY: str = "aduanadoc_programmer_secret_key_2026"

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

