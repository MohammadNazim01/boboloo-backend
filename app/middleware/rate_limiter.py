from fastapi import Request
from fastapi.responses import JSONResponse
from app.core.redis import redis_client
from app.core.firebase import verify_firebase_token
import hashlib
import logging

logger = logging.getLogger("rate_limit")

RATE_LIMIT_CONFIG = {
    "toy": (20, 60),     # 🧸 toy
    "user": (60, 60),    # 👨 user
    "ip": (100, 60),     # 🌐 fallback
}


async def get_identifier(request: Request):

    path = request.url.path

    # =========================
    # 🧸 TOY (REDIS FAST)
    # =========================
    if path.startswith("/api/v1/toy/runtime"):

        toy_key = request.headers.get("x-toy-key")

        if toy_key:
            try:
                key_hash = hashlib.sha256(
                    toy_key.strip().encode()
                ).hexdigest()

                toy_id = await redis_client.get(f"toy_key:{key_hash}")

                if toy_id:
                    return f"toy:{toy_id}", "toy"

            except Exception as e:
                logger.error(f"Redis toy lookup failed: {e}")

        return "anonymous", "ip"

    # =========================
    # 👨 USER (FIREBASE)
    # =========================
    auth_header = request.headers.get("authorization")

    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header.split(" ")[1]

        try:
            decoded = verify_firebase_token(token)
            uid = decoded.get("uid")

            if uid:
                return f"user:{uid}", "user"

        except Exception as e:
            logger.warning(f"Invalid Firebase token: {e}")

    # =========================
    # 🌐 IP fallback
    # =========================
    ip = request.client.host or "anonymous"
    return f"ip:{ip}", "ip"


async def rate_limit_middleware(request: Request, call_next):

    # skip health/public
    if request.url.path in ["/", "/health"]:
        return await call_next(request)

    try:
        identifier, id_type = await get_identifier(request)

        RATE_LIMIT, WINDOW = RATE_LIMIT_CONFIG[id_type]

        key = f"rate:{identifier}:{request.url.path}"

        # Atomic increment first — avoids the GET→check→INCR race where two
        # concurrent requests both read count < limit and both pass.
        new_value = await redis_client.incr(key)

        if new_value == 1:
            # First request in this window: set the expiry.
            await redis_client.expire(key, WINDOW)

        # =========================
        # RATE LIMIT HIT
        # =========================
        if new_value > RATE_LIMIT:

            logger.warning(
                f"Rate limit hit | id={identifier} | type={id_type} | path={request.url.path}"
            )

            return JSONResponse(
                status_code=429,
                content={
                    "detail": "Too many requests. Please slow down."
                }
            )

        response = await call_next(request)

        remaining = max(RATE_LIMIT - new_value, 0)
        response.headers["X-RateLimit-Remaining"] = str(remaining)

        return response

    except Exception as e:
        logger.error(f"Rate limiter error: {e}")
        return await call_next(request)