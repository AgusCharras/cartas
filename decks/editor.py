import re

from .importer import MAX_CARDS_PER_KIND, MAX_TEXT_LEN
from .models import Card

MAX_LINES_PER_SUBMIT = 600


def parse_lines(raw, kind):
    """Una carta por línea. Devuelve ([(texto, pick)], [errores])."""
    label = "negra" if kind == Card.BLACK else "blanca"
    lines = raw.splitlines()
    if len(lines) > MAX_LINES_PER_SUBMIT:
        return [], [f"Demasiadas líneas de una vez (máx. {MAX_LINES_PER_SUBMIT})."]

    cards, errors = [], []
    for n, line in enumerate(lines, start=1):
        text = " ".join(line.split())  # normaliza espacios
        if not text:
            continue
        if len(text) > MAX_TEXT_LEN:
            errors.append(f"Carta {label}, línea {n}: demasiado larga (máx. {MAX_TEXT_LEN}).")
            continue
        pick = 1
        if kind == Card.BLACK:
            text = re.sub(r"_+", "_", text)  # "_____" cuenta como un solo hueco
            pick = text.count("_")
            if pick == 0:
                errors.append(f"Carta negra, línea {n}: falta el hueco «_».")
                continue
            if pick > 3:
                errors.append(f"Carta negra, línea {n}: máximo 3 huecos.")
                continue
        cards.append((text, pick))
    return cards, errors


def build_cards(deck, white_raw, black_raw):
    """Valida y arma las cartas nuevas. Devuelve (cartas_a_crear, errores, repetidas_omitidas)."""
    white, errors = parse_lines(white_raw, Card.WHITE)
    black, errors_b = parse_lines(black_raw, Card.BLACK)
    errors += errors_b
    if errors:
        return [], errors, 0

    existing = {(c.kind, c.text.lower()) for c in deck.cards.all()}
    totals = {Card.WHITE: 0, Card.BLACK: 0}
    for kind, _ in existing:
        totals[kind] += 1

    to_create, skipped = [], 0
    for kind, items in ((Card.WHITE, white), (Card.BLACK, black)):
        for text, pick in items:
            key = (kind, text.lower())
            if key in existing:
                skipped += 1
                continue
            existing.add(key)
            totals[kind] += 1
            to_create.append(Card(deck=deck, kind=kind, text=text, pick=pick))

    for kind, label in ((Card.WHITE, "blancas"), (Card.BLACK, "negras")):
        if totals[kind] > MAX_CARDS_PER_KIND:
            errors.append(f"Máximo {MAX_CARDS_PER_KIND} cartas {label} por mazo.")
    return ([] if errors else to_create), errors, skipped