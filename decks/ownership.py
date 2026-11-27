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