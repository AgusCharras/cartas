import re
from datetime import timedelta

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.utils import timezone

from decks.models import Card, Deck
from game.models import Game


class Command(BaseCommand):
    help = ("Borra salas inactivas, mazos privados sin uso y sesiones vencidas, "
            "y corrige cuántas respuestas pide cada carta negra según sus huecos.")

    def add_arguments(self, parser):
        parser.add_argument("--game-hours", type=int, default=6)
        parser.add_argument("--deck-days", type=int, default=90)

    def handle(self, *args, **opts):
        now = timezone.now()
        game_cut = now - timedelta(hours=opts["game_hours"])
        deck_cut = now - timedelta(days=opts["deck_days"])

        # Salas viejas en las que nadie estuvo conectado recientemente (incluye salas sin jugadores)
        games = Game.objects.filter(created_at__lt=game_cut).exclude(players__last_seen__gte=game_cut)
        n_games = games.count()
        games.delete()

        # Mazos privados que hace mucho no se editan ni se usan en ninguna sala
        decks = Deck.objects.filter(is_public=False, updated_at__lt=deck_cut, game__isnull=True)
        n_decks = decks.count()
        decks.delete()

        # Una negra pide una respuesta por hueco "_". Los mazos cargados antes con un `pick`
        # incorrecto (por ejemplo, importados desde un JSON sin ese campo) quedan corregidos.
        n_picks = 0
        for card in Card.objects.filter(kind=Card.BLACK).only("id", "text", "pick").iterator():
            blanks = len(re.findall(r"_+", card.text))
            if 1 <= blanks <= 3 and card.pick != blanks:
                Card.objects.filter(pk=card.pk).update(pick=blanks)
                n_picks += 1

        call_command("clearsessions")
        self.stdout.write(
            f"Salas borradas: {n_games} · Mazos borrados: {n_decks} · Negras corregidas: {n_picks}")