"""
Settings API endpoints.
"""

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..services import settings_service

router = APIRouter(prefix="/api/settings", tags=["settings"])


class ADSKeyRequest(BaseModel):
    api_key: str


class ADSKeyStatus(BaseModel):
    configured: bool
    valid: Optional[bool] = None
    message: Optional[str] = None


class ADSKeyValidation(BaseModel):
    valid: bool
    message: str


@router.get("/ads-key/status", response_model=ADSKeyStatus)
async def get_ads_key_status():
    """Check if ADS API key is configured."""
    has_key = settings_service.has_ads_api_key()
    return ADSKeyStatus(configured=has_key)


@router.post("/ads-key", response_model=ADSKeyStatus)
async def set_ads_key(request: ADSKeyRequest):
    """Set the ADS API key (will be stored encrypted)."""
    api_key = request.api_key.strip()

    if not api_key:
        # Clear the key
        settings_service.set_ads_api_key("")
        return ADSKeyStatus(configured=False)

    # Validate the key first
    is_valid, message = settings_service.validate_ads_api_key(api_key)

    if not is_valid:
        raise HTTPException(status_code=400, detail=message)

    # Save the key
    settings_service.set_ads_api_key(api_key)

    return ADSKeyStatus(configured=True, valid=True, message=message)


@router.delete("/ads-key")
async def delete_ads_key():
    """Remove the stored ADS API key."""
    settings_service.set_ads_api_key("")
    return {"status": "deleted"}


@router.post("/ads-key/validate", response_model=ADSKeyValidation)
async def validate_ads_key(request: ADSKeyRequest):
    """Validate an ADS API key without saving it."""
    is_valid, message = settings_service.validate_ads_api_key(request.api_key.strip())
    return ADSKeyValidation(valid=is_valid, message=message)
