from functools import wraps
from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse


def client_ip(request):
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff and settings.TRUSTED_PROXY_COUNT:
        parts = [p.strip() for p in xff.split(",")]
        # Solo confiamos en lo que agregaron NUESTROS proxies (los últimos de la lista)
        return parts[max(len(parts) - settings.TRUSTED_PROXY_COUNT, 0)]
    return request.META.get("REMOTE_ADDR", "")


def rate_limit(name, limit, window):
    """Máximo `limit` POSTs cada `window` segundos por IP."""
    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            if request.method == "POST":
                key = f"rl:{name}:{client_ip(request)}"
                cache.add(key, 0, window)
                try:
                    count = cache.incr(key)
                except ValueError:  # la clave venció justo en el medio
                    cache.set(key, 1, window)
                    count = 1
                if count > limit:
                    return HttpResponse(
                        "Demasiados intentos. Probá de nuevo en unos minutos.", status=429)
            return view(request, *args, **kwargs)
        return wrapper
    return decorator