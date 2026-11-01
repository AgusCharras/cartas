from datetime import timedelta

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.utils import timezone

from decks.models import Deck
from game.models import Game


class Command(BaseCommand):
    help = "Borra salas inactivas, mazos privados viejos y sesiones vencidas."

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

        # Mazos privados viejos que ya no usa ninguna sala
        decks = Deck.objects.filter(is_public=False, created_at__lt=deck_cut, game__isnull=True)
        n_decks = decks.count()
        decks.delete()

        call_command("clearsessions")
        self.stdout.write(f"Salas borradas: {n_games} · Mazos borrados: {n_decks}")