import base64
import uuid

from .models import Deck

OWNER_COOKIE = "cartas_owner"
OWNER_COOKIE_AGE = 60 * 60 * 24 * 365  # 1 año


def _valid(value):
    """Un token válido es un UUID (así no se aceptan valores raros que mande el navegador)."""
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, AttributeError, TypeError):
        return None


def get_token(request, create=False):
    """Token de dueño de mazos. Vive en la sesión y también en una cookie propia de 1 año
    (ver OwnerCookieMiddleware): si la sesión se pierde, el token se recupera de la cookie."""
    token = request.session.get("uploader_token") or _valid(request.COOKIES.get(OWNER_COOKIE))
    if token and request.session.get("uploader_token") != token:
        request.session["uploader_token"] = token
    if not token and create:
        token = str(uuid.uuid4())
        request.session["uploader_token"] = token
    return token


def my_decks(request):
    token = get_token(request)
    return Deck.objects.filter(uploader_token=token) if token else Deck.objects.none()


# --- Código de recuperación -------------------------------------------------
# El código es el propio token de dueño escrito en base32 (26 letras/números, en grupos de 4).
# No se guarda nada extra en la base: quien tiene el código puede "adoptar" esa identidad en otro
# dispositivo. Por eso es largo (128 bits): no se puede adivinar.

def token_to_code(token):
    raw = uuid.UUID(str(token)).bytes
    text = base64.b32encode(raw).decode().rstrip("=")
    return "-".join(text[i:i + 4] for i in range(0, len(text), 4))


def code_to_token(code):
    """Devuelve el token (str UUID) o None si el código no es válido."""
    text = "".join(ch for ch in str(code or "").upper() if ch.isalnum())
    if len(text) != 26:
        return None
    try:
        raw = base64.b32decode(text + "=" * 6)
        return str(uuid.UUID(bytes=raw))
    except (ValueError, TypeError):
        return None


def adopt_token(request, new_token):
    """Pasa este navegador a la identidad `new_token`. Los mazos que este navegador tenía
    con otra identidad se suman a la adoptada (no se pierden). Devuelve cuántos se movieron."""
    old = get_token(request)
    moved = 0
    if old and old != new_token:
        moved = Deck.objects.filter(uploader_token=old).update(uploader_token=new_token)
    request.session["uploader_token"] = new_token
    return moved