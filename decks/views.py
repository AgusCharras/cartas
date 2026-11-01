import json

from django import forms
from django.contrib import messages
from django.db.models import Count, Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from config.ratelimit import rate_limit
from game.models import Game
from .editor import build_cards
from .importer import DeckImportError, import_deck
from .models import Card, Deck
from .ownership import get_token, my_decks

MAX_DECKS_PER_USER = 20


class ImportForm(forms.Form):
    file = forms.FileField()


class DeckForm(forms.Form):
    name = forms.CharField(max_length=100, min_length=2, strip=True)
    description = forms.CharField(max_length=300, required=False, strip=True)


def in_active_game(deck):
    """Borrar cartas de un mazo en uso rompería la partida en curso."""
    return Game.objects.filter(decks=deck, state__in=[Game.PLAYING, Game.JUDGING]).exists()


def deck_list(request):
    decks = my_decks(request).annotate(
        whites=Count("cards", filter=Q(cards__kind=Card.WHITE)),
        blacks=Count("cards", filter=Q(cards__kind=Card.BLACK)),
    ).order_by("-created_at")
    return render(request, "decks/list.html", {"decks": decks})


@rate_limit("import", limit=10, window=3600)
def import_view(request):
    form = ImportForm(request.POST or None, request.FILES or None)
    error = None
    if request.method == "POST" and form.is_valid():
        if my_decks(request).count() >= MAX_DECKS_PER_USER:
            error = f"Llegaste al máximo de {MAX_DECKS_PER_USER} mazos. Borrá alguno."
        else:
            token = get_token(request, create=True)
            try:
                import_deck(form.cleaned_data["file"].read(), uploader_token=token)
            except DeckImportError as e:
                error = str(e)
            else:
                messages.success(request, "Mazo importado.")
                return redirect("decks:list")
    return render(request, "decks/import.html", {"form": form, "error": error})


@rate_limit("deck_new", limit=20, window=3600)
def deck_new(request):
    form = DeckForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        if my_decks(request).count() >= MAX_DECKS_PER_USER:
            form.add_error(None, f"Llegaste al máximo de {MAX_DECKS_PER_USER} mazos.")
        else:
            deck = Deck.objects.create(
                name=form.cleaned_data["name"],
                description=form.cleaned_data["description"],
                uploader_token=get_token(request, create=True),
            )
            return redirect("decks:edit", pk=deck.pk)
    return render(request, "decks/new.html", {"form": form})


@rate_limit("deck_edit", limit=150, window=600)
def deck_edit(request, pk):
    deck = get_object_or_404(my_decks(request), pk=pk)  # solo el dueño
    errors = []
    values = {"white": "", "black": ""}

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "details":
            form = DeckForm(request.POST)
            if form.is_valid():
                deck.name = form.cleaned_data["name"]
                deck.description = form.cleaned_data["description"]
                deck.save()
                messages.success(request, "Datos guardados.")
                return redirect("decks:edit", pk=deck.pk)
            errors.append("El nombre debe tener entre 2 y 100 caracteres.")

        elif action == "add_cards":
            values = {"white": request.POST.get("white", ""), "black": request.POST.get("black", "")}
            to_create, errors, skipped = build_cards(deck, values["white"], values["black"])
            if not errors:
                Card.objects.bulk_create(to_create)
                deck.save()  # actualiza updated_at
                msg = f"Se agregaron {len(to_create)} cartas."
                if skipped:
                    msg += f" Se omitieron {skipped} repetidas."
                messages.success(request, msg)
                return redirect("decks:edit", pk=deck.pk)

        elif action == "delete_cards":
            if in_active_game(deck):
                errors.append("El mazo se está usando en una partida en curso.")
            else:
                ids = [int(i) for i in request.POST.getlist("delete")[:1100]
                       if i.isdigit() and len(i) <= 9]
                qs = deck.cards.filter(id__in=ids)
                n = qs.count()
                qs.delete()
                deck.save()
                messages.success(request, f"Se borraron {n} cartas.")
                return redirect("decks:edit", pk=deck.pk)

        elif action == "toggle_share":
            deck.is_shared = not deck.is_shared
            deck.save()
            if deck.is_shared:
                deck.ensure_share_code()
            messages.success(request, "Ahora el mazo es compartible." if deck.is_shared
                             else "Dejaste de compartir el mazo.")
            return redirect("decks:edit", pk=deck.pk)

    share_url = None
    if deck.is_shared and deck.share_code:
        share_url = request.build_absolute_uri(reverse("decks:preview", args=[deck.share_code]))
    return render(request, "decks/edit.html", {
        "deck": deck,
        "whites": deck.cards.filter(kind=Card.WHITE).order_by("id"),
        "blacks": deck.cards.filter(kind=Card.BLACK).order_by("id"),
        "errors": errors,
        "values": values,
        "share_url": share_url,
    })


def deck_export(request, pk):
    deck = get_object_or_404(my_decks(request), pk=pk)
    data = {
        "name": deck.name,
        "description": deck.description,
        "black": [{"text": c.text, "pick": c.pick}
                  for c in deck.cards.filter(kind=Card.BLACK).order_by("id")],
        "white": [c.text for c in deck.cards.filter(kind=Card.WHITE).order_by("id")],
    }
    resp = HttpResponse(json.dumps(data, ensure_ascii=False, indent=2),
                        content_type="application/json; charset=utf-8")
    resp["Content-Disposition"] = f'attachment; filename="{slugify(deck.name) or "mazo"}.json"'
    return resp


@require_POST
def deck_delete(request, pk):
    deck = get_object_or_404(my_decks(request), pk=pk)
    if in_active_game(deck):
        messages.error(request, "El mazo se está usando en una partida en curso.")
        return redirect("decks:edit", pk=deck.pk)
    deck.delete()
    messages.success(request, "Mazo borrado.")
    return redirect("decks:list")


def deck_preview(request, code):
    """Vista de solo lectura de un mazo compartido (se entra con el código)."""
    deck = get_object_or_404(Deck, share_code=code.upper())
    token = get_token(request)
    is_owner = bool(token and deck.uploader_token and str(deck.uploader_token) == token)
    if not (deck.is_shared or deck.is_public or is_owner):
        raise Http404
    return render(request, "decks/preview.html", {
        "deck": deck,
        "whites": deck.cards.filter(kind=Card.WHITE).order_by("id"),
        "blacks": deck.cards.filter(kind=Card.BLACK).order_by("id"),
    })