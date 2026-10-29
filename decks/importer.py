import json
from django.db import transaction
from .models import Deck, Card

MAX_FILE_BYTES = 200 * 1024
MAX_CARDS_PER_KIND = 500
MAX_TEXT_LEN = 300


class DeckImportError(Exception):
    pass


def parse_deck(raw: bytes) -> dict:
    if len(raw) > MAX_FILE_BYTES:
        raise DeckImportError("El archivo es demasiado grande (máx. 200 KB).")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise DeckImportError("El archivo no es un JSON válido en UTF-8.")

    if not isinstance(data, dict):
        raise DeckImportError("El JSON debe ser un objeto con 'name', 'black' y 'white'.")

    name = str(data.get("name", "")).strip()[:100]
    if not name:
        raise DeckImportError("Falta el campo 'name'.")

    black, white = data.get("black", []), data.get("white", [])
    if not isinstance(black, list) or not isinstance(white, list):
        raise DeckImportError("'black' y 'white' deben ser listas.")
    if len(black) > MAX_CARDS_PER_KIND or len(white) > MAX_CARDS_PER_KIND:
        raise DeckImportError(f"Máximo {MAX_CARDS_PER_KIND} cartas por tipo.")

    clean_black = []
    for i, item in enumerate(black, 1):
        text = str(item.get("text", "") if isinstance(item, dict) else item).strip()
        pick = item.get("pick", 1) if isinstance(item, dict) else 1
        if not text or len(text) > MAX_TEXT_LEN:
            raise DeckImportError(f"Carta negra #{i}: texto vacío o demasiado largo.")
        if "_" not in text:
            raise DeckImportError(f"Carta negra #{i}: tiene que tener al menos un '_'.")
        if pick not in (1, 2, 3):
            raise DeckImportError(f"Carta negra #{i}: 'pick' debe ser 1, 2 o 3.")
        clean_black.append((text, pick))

    clean_white = []
    for i, item in enumerate(white, 1):
        text = str(item).strip()
        if not text or len(text) > MAX_TEXT_LEN:
            raise DeckImportError(f"Carta blanca #{i}: texto vacío o demasiado largo.")
        clean_white.append(text)

    if not clean_black or len(clean_white) < 20:
        raise DeckImportError("El mazo necesita al menos 1 carta negra y 20 blancas.")

    return {"name": name, "description": str(data.get("description", ""))[:300],
            "black": clean_black, "white": clean_white}


@transaction.atomic
def import_deck(raw: bytes, uploader_token=None, is_public=False) -> Deck:
    parsed = parse_deck(raw)
    deck = Deck.objects.create(
        name=parsed["name"], description=parsed["description"],
        is_public=is_public, uploader_token=uploader_token,
    )
    cards = [Card(deck=deck, kind=Card.BLACK, text=t, pick=p) for t, p in parsed["black"]]
    cards += [Card(deck=deck, kind=Card.WHITE, text=t) for t in parsed["white"]]
    Card.objects.bulk_create(cards)
    return deck