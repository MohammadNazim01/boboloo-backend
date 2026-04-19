from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from app.core.firebase import verify_firebase_token


security = HTTPBearer()


async def get_current_admin(
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """
    Verifies Firebase token
    Allows ONLY admin users
    """

    try:
        decoded_token = verify_firebase_token(
            credentials.credentials
        )

    except Exception:
        raise HTTPException(
            status_code=401,
            detail="Invalid Firebase token",
        )

    # ✅ ADMIN ROLE CHECK
    role = decoded_token.get("role")

    if role != "admin":
        raise HTTPException(
            status_code=403,
            detail="Admin access required",
        )

    return decoded_token