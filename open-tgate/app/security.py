import hmac

from fastapi import Header, HTTPException, status

from .config import get_settings


def require_admin(authorization: str | None = Header(default=None)) -> None:
    expected = get_settings().api_admin_token
    supplied = authorization.removeprefix("Bearer ") if authorization else ""
    if not expected or not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unauthorized")

