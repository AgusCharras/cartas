from django import forms
from django.db import IntegrityError
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from config.ratelimit import rate_limit
from decks.models import Deck
from .models import Game, Player
from .session import get_player, remember_player


class NicknameForm(forms.Form):
    nickname = forms.CharField(max_length=24, min_length=2, strip=True)


def available_decks(request):
    token = request.session.get("uploader_token")
    q = Q(is_public=True)
    if token:
        q |= Q(uploader_token=token)
    return Deck.objects.filter(q).order_by("name")


@rate_limit("home", limit=20, window=600)  # 20 por 10 min
def home(request):
    """Crear sala o entrar con código."""
    decks = available_decks(request)

    if request.method == "POST" and "create" in request.POST:
        game = Game.objects.create()
        game.decks.set(decks.filter(id__in=request.POST.getlist("decks")))
        return redirect("game:join", code=game.code)

    if request.method == "POST" and "code" in request.POST:
        return redirect("game:join", code=request.POST["code"].strip().upper())

    return render(request, "game/home.html", {"decks": decks})


@rate_limit("join", limit=30, window=600)  # 30 por 10 min
def join(request, code):
    """Esta es la URL que se comparte como invitación: /sala/ABC12/"""
    game = get_object_or_404(Game, code=code.upper())

    # ¿Ya estaba en esta sala? Entra directo.
    if get_player(request, game):
        return redirect("game:room", code=game.code)

    if game.state != Game.LOBBY:
        return render(request, "game/closed.html", {"game": game}, status=403)
    if game.players.count() >= game.max_players:
        return render(request, "game/full.html", {"game": game}, status=403)

    form = NicknameForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            player = Player.objects.create(
                game=game,
                nickname=form.cleaned_data["nickname"],
                is_host=not game.players.exists(),  # el primero en entrar es el host
            )
        except IntegrityError:
            form.add_error("nickname", "Ese nombre ya está en uso en la sala.")
        else:
            remember_player(request, player)
            return redirect("game:room", code=game.code)

    return render(request, "game/join.html", {"game": game, "form": form})


def room(request, code):
    game = get_object_or_404(Game, code=code.upper())
    player = get_player(request, game)
    if not player:
        return redirect("game:join", code=game.code)  # sin identidad, pasa por el nickname
    deck_options = [{"id": d.id, "name": d.name} for d in available_decks(request)]
    return render(request, "game/room.html",
                  {"game": game, "player": player, "deck_options": deck_options})