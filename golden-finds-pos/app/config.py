"""
Configuration.

The shop machine is not a cloud server - nobody is going to set
environment variables before opening the till in the morning. So the
secret key is generated once and kept in the instance folder, and
everything else has a working default. Environment variables still win
where they are set, which is what makes testing and deployment easy.
"""

import os
import secrets
from datetime import timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
INSTANCE_DIR = BASE_DIR / "instance"


def _secret_key():
    """
    Reads the signing key, creating it on first run.

    This key signs session cookies. If it changed on every restart, every
    cashier would be logged out whenever the machine rebooted.
    """
    env = os.environ.get("SECRET_KEY")
    if env:
        return env

    INSTANCE_DIR.mkdir(parents=True, exist_ok=True)
    key_file = INSTANCE_DIR / "secret_key"
    if key_file.exists():
        return key_file.read_text(encoding="utf-8").strip()

    key = secrets.token_hex(32)
    key_file.write_text(key, encoding="utf-8")
    try:
        os.chmod(key_file, 0o600)
    except OSError:
        pass  # Windows may not honour this; the file is still outside the repo.
    return key


class Config:
    SECRET_KEY = _secret_key()
    DATABASE = os.environ.get("DATABASE", str(INSTANCE_DIR / "golden_finds.db"))

    # A till is walked away from. Log out after inactivity so the next
    # person cannot ring up a sale under someone else's name.
    PERMANENT_SESSION_LIFETIME = timedelta(
        minutes=int(os.environ.get("SESSION_MINUTES", 60))
    )
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    # Only set this when actually served over HTTPS; on a LAN till it is not.
    SESSION_COOKIE_SECURE = os.environ.get("HTTPS", "").lower() in ("1", "true", "yes")

    # Brute-forcing a 4-digit PIN is trivial without a lockout.
    MAX_PIN_ATTEMPTS = int(os.environ.get("MAX_PIN_ATTEMPTS", 5))
    LOCKOUT_SECONDS = int(os.environ.get("LOCKOUT_SECONDS", 300))

    SHOP_NAME = os.environ.get("SHOP_NAME", "Golden Finds")

    # Where `flask backup` writes to. Point this at a OneDrive or Google
    # Drive folder and the sync client carries the copy off the machine -
    # which is the whole point of taking it.
    BACKUP_DIR = os.environ.get("BACKUP_DIR", str(BASE_DIR / "backups"))

    # The dashboard nags once backups are older than this. A backup job
    # that quietly stopped running is the usual way a shop like this
    # loses its data.
    BACKUP_WARN_AFTER_HOURS = int(os.environ.get("BACKUP_WARN_AFTER_HOURS", 48))


class TestConfig(Config):
    TESTING = True
    DATABASE = ":memory:"
    SECRET_KEY = "test-key-not-for-real-use"
    WTF_CSRF_ENABLED = False
