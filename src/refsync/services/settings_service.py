"""
Settings management with encrypted storage for sensitive values.
"""

import base64
import json
import os
from typing import Optional

from cryptography.fernet import Fernet

from ..config import settings as app_settings

# Settings file location (in user data directory)
SETTINGS_FILE = app_settings.data_dir / "settings.json"
ENCRYPTION_KEY_FILE = app_settings.data_dir / ".encryption_key"


def _get_or_create_encryption_key() -> bytes:
    """
    Get or create a machine-specific encryption key.
    Stored in a hidden file in the data directory.
    """
    if ENCRYPTION_KEY_FILE.exists():
        return ENCRYPTION_KEY_FILE.read_bytes()

    # Generate a new key
    key = Fernet.generate_key()

    # Ensure directory exists
    ENCRYPTION_KEY_FILE.parent.mkdir(parents=True, exist_ok=True)

    # Save with restricted permissions
    ENCRYPTION_KEY_FILE.write_bytes(key)
    try:
        os.chmod(ENCRYPTION_KEY_FILE, 0o600)  # Owner read/write only
    except OSError:
        pass  # Windows may not support chmod

    return key


def _get_fernet() -> Fernet:
    """Get Fernet instance for encryption/decryption."""
    key = _get_or_create_encryption_key()
    return Fernet(key)


def encrypt_value(value: str) -> str:
    """Encrypt a string value."""
    if not value:
        return ""
    f = _get_fernet()
    encrypted = f.encrypt(value.encode())
    return base64.urlsafe_b64encode(encrypted).decode()


def decrypt_value(encrypted: str) -> str:
    """Decrypt an encrypted string value."""
    if not encrypted:
        return ""
    try:
        f = _get_fernet()
        decoded = base64.urlsafe_b64decode(encrypted.encode())
        return f.decrypt(decoded).decode()
    except Exception:
        return ""


def _load_settings() -> dict:
    """Load settings from file."""
    if not SETTINGS_FILE.exists():
        return {}
    try:
        return json.loads(SETTINGS_FILE.read_text())
    except (json.JSONDecodeError, IOError):
        return {}


def _save_settings(data: dict) -> None:
    """Save settings to file."""
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(data, indent=2))


def get_ads_api_key() -> Optional[str]:
    """Get the decrypted ADS API key, or None if not set."""
    data = _load_settings()
    encrypted_key = data.get("ads_api_key_encrypted")
    if not encrypted_key:
        return None
    return decrypt_value(encrypted_key)


def set_ads_api_key(api_key: str) -> None:
    """Set and encrypt the ADS API key."""
    data = _load_settings()
    if api_key:
        data["ads_api_key_encrypted"] = encrypt_value(api_key)
    else:
        data.pop("ads_api_key_encrypted", None)
    _save_settings(data)


def has_ads_api_key() -> bool:
    """Check if ADS API key is configured."""
    return bool(get_ads_api_key())


def get_setting(key: str, default=None):
    """Get a non-sensitive setting value."""
    data = _load_settings()
    return data.get(key, default)


def set_setting(key: str, value) -> None:
    """Set a non-sensitive setting value."""
    data = _load_settings()
    data[key] = value
    _save_settings(data)


def delete_setting(key: str) -> None:
    """Delete a setting."""
    data = _load_settings()
    data.pop(key, None)
    _save_settings(data)


# Validation
def validate_ads_api_key(api_key: str) -> tuple[bool, str]:
    """
    Validate an ADS API key by making a test request.

    Returns:
        Tuple of (is_valid, message)
    """
    import httpx

    if not api_key or len(api_key) < 10:
        return False, "API key appears to be invalid (too short)"

    try:
        # Make a simple search request to validate
        response = httpx.get(
            "https://api.adsabs.harvard.edu/v1/search/query",
            params={"q": "test", "rows": 1},
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10.0,
        )

        if response.status_code == 200:
            return True, "API key is valid"
        elif response.status_code == 401:
            return False, "Invalid API key"
        elif response.status_code == 403:
            return False, "API key lacks required permissions"
        else:
            return False, f"Unexpected response: {response.status_code}"

    except httpx.TimeoutException:
        return False, "Connection to ADS timed out"
    except httpx.RequestError as e:
        return False, f"Connection error: {str(e)}"
