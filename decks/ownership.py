import uuid

from .models import Deck


def get_token(request, create=False):
    token = request.session.get("uploader_token")
    if not token and create:
        token = str(uuid.uuid4())
        request.session["uploader_token"] = token
    return token


def my_decks(request):
    token = get_token(request)
    return Deck.objects.filter(uploader_token=token) if token else Deck.objects.none()