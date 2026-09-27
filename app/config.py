"""Loads settings from the .env file so secrets never live in source code."""
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

APP_DIR = Path(__file__).resolve().parent          # .../mini_olx/app
PROJECT_ROOT = APP_DIR.parent                      # .../mini_olx

load_dotenv(PROJECT_ROOT / ".env")


def _required(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(
            f"Missing required setting '{name}'. Add it to your .env file "
            f"(see .env.example)."
        )
    return value


def _bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() == "true"


@dataclass(frozen=True)
class Settings:
    DB_HOST: str
    DB_PORT: int
    DB_USER: str
    DB_PASSWORD: str
    DB_NAME: str
    SECRET_KEY: str
    UPI_ID: str
    UPI_NAME: str
    THEME: str
    DB_ECHO: bool
    SESSION_DAYS: int
    COOKIE_SECURE: bool
    RAZORPAY_KEY_ID: str
    RAZORPAY_KEY_SECRET: str
    RAZORPAY_WEBHOOK_SECRET: str
    GOOGLE_CLIENT_ID: str
    GOOGLE_CLIENT_SECRET: str
    GOOGLE_REDIRECT_URI: str


settings = Settings(
    DB_HOST=os.getenv("DB_HOST", "localhost"),
    DB_PORT=int(os.getenv("DB_PORT", "3306")),
    DB_USER=_required("DB_USER"),
    DB_PASSWORD=_required("DB_PASSWORD"),
    DB_NAME=_required("DB_NAME"),
    SECRET_KEY=_required("SECRET_KEY"),
    UPI_ID=os.getenv("UPI_ID", "example@upi").strip(),
    UPI_NAME=os.getenv("UPI_NAME", "Mini OLX").strip(),
    THEME=os.getenv("THEME", "premium").strip().lower(),
    DB_ECHO=_bool("DB_ECHO"),
    SESSION_DAYS=int(os.getenv("SESSION_DAYS", "7")),
    COOKIE_SECURE=_bool("COOKIE_SECURE"),
    RAZORPAY_KEY_ID=os.getenv("RAZORPAY_KEY_ID", "").strip(),
    RAZORPAY_KEY_SECRET=os.getenv("RAZORPAY_KEY_SECRET", "").strip(),
    RAZORPAY_WEBHOOK_SECRET=os.getenv("RAZORPAY_WEBHOOK_SECRET", "").strip(),
    GOOGLE_CLIENT_ID=os.getenv("GOOGLE_CLIENT_ID", "").strip(),
    GOOGLE_CLIENT_SECRET=os.getenv("GOOGLE_CLIENT_SECRET", "").strip(),
    GOOGLE_REDIRECT_URI=os.getenv("GOOGLE_REDIRECT_URI", "http://127.0.0.1:8000/auth/google/callback").strip(),
)
