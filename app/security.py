import secrets
from typing import Annotated

from fastapi import Depends, HTTPException
from fastapi.security import APIKeyHeader

from app.config import settings

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def require_api_key(api_key: Annotated[str | None, Depends(api_key_header)]) -> None:
    if api_key is None or not is_known_key(api_key):
        raise HTTPException(401, "Missing or invalid API key", headers={"WWW-Authenticate": "ApiKey"})


def is_known_key(api_key: str) -> bool:
    # compare_digest takes the same time whatever the input, so the response time does
    # not reveal how much of a key was right (ADR-16). Bytes, because it rejects non-ASCII str.
    candidate = api_key.encode()
    return any(secrets.compare_digest(candidate, key.encode()) for key in settings.api_keys)
