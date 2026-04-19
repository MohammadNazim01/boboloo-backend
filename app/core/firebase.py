import firebase_admin
from firebase_admin import credentials, auth
from app.core.config import settings


def init_firebase():

    # Prevent double init
    if firebase_admin._apps:
        return

    # ===============================
    # LOCAL DEV
    # ===============================
    if settings.FIREBASE_CREDENTIALS_PATH:
        cred = credentials.Certificate(
            settings.FIREBASE_CREDENTIALS_PATH
        )
        firebase_admin.initialize_app(cred)

    # ===============================
    # CLOUD RUN
    # ===============================
    else:
        firebase_admin.initialize_app()


init_firebase()


# =====================================
# TOKEN VERIFY (HARDENED)
# =====================================
def verify_firebase_token(token: str):

    decoded = auth.verify_id_token(
        token,
        check_revoked=True,
    )

    if "uid" not in decoded:
        raise Exception("Invalid Firebase token")

    return decoded