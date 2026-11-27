from django.conf import settings

from .ownership import OWNER_COOKIE, OWNER_COOKIE_AGE, _valid


class OwnerCookieMiddleware:
    """Guarda el token de dueño de mazos en una cookie de 1 año, aparte de la sesión.

    - Al entrar: si la sesión no tiene token pero la cookie sí, se lo devuelve a la sesión
      (así también lo ven las salas por websocket, que leen la sesión).
    - Al salir: si la sesión tiene token y la cookie no coincide, la escribe.
    Va justo después de SessionMiddleware."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        cookie = _valid(request.COOKIES.get(OWNER_COOKIE))
        if cookie and not request.session.get("uploader_token"):
            request.session["uploader_token"] = cookie
        response = self.get_response(request)
        token = request.session.get("uploader_token")
        if token and token != cookie:
            response.set_cookie(
                OWNER_COOKIE, token, max_age=OWNER_COOKIE_AGE, httponly=True, samesite="Lax",
                secure=getattr(settings, "SESSION_COOKIE_SECURE", False))
        return response