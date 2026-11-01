from django.db import models

import secrets
import uuid
from django.db import models
from decks.models import Deck
from django.utils import timezone

# Sin 0/O, 1/I/L para que el código sea fácil de dictar por WhatsApp/Discord
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def generate_code():
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(5))

REROLLS_PER_GAME = 2

class Game(models.Model):
    LOBBY, PLAYING, JUDGING, FINISHED = "lobby", "playing", "judging", "finished"
    STATE_CHOICES = [
        (LOBBY, "Lobby"), (PLAYING, "Jugando"),
        (JUDGING, "Eligiendo ganadora"), (FINISHED, "Terminada"),
    ]

    code = models.CharField(max_length=6, unique=True, default=generate_code)
    decks = models.ManyToManyField(Deck, blank=True)
    state = models.CharField(max_length=10, choices=STATE_CHOICES, default=LOBBY)
    max_players = models.PositiveSmallIntegerField(default=10)
    points_to_win = models.PositiveSmallIntegerField(default=7)
    created_at = models.DateTimeField(auto_now_add=True)
    judge = models.ForeignKey("Player", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    round_number = models.PositiveSmallIntegerField(default=0)
    last_result = models.JSONField(null=True, blank=True)  # quién ganó la última ronda

    def __str__(self):
        return self.code


class Player(models.Model):
    game = models.ForeignKey(Game, related_name="players", on_delete=models.CASCADE)
    # Este token es la "identidad" del jugador. Vive en su sesión, nunca se muestra a otros.
    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    nickname = models.CharField(max_length=24)
    is_host = models.BooleanField(default=False)
    score = models.PositiveSmallIntegerField(default=0)
    joined_at = models.DateTimeField(auto_now_add=True)
    connections = models.PositiveSmallIntegerField(default=0)  # pestañas/sockets abiertos
    last_seen = models.DateTimeField(default=timezone.now)
    rerolls_left = models.PositiveSmallIntegerField(default=REROLLS_PER_GAME)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["game", "nickname"], name="unique_nick_per_game")
        ]

    def __str__(self):
        return f"{self.nickname} ({self.game.code})"
    
class GameCard(models.Model):
    """Una carta dentro de una partida concreta (mazo de robo, mano, jugada o descarte)."""
    DECK, HAND, PLAYED, CURRENT, DISCARD = "deck", "hand", "played", "current", "discard"

    game = models.ForeignKey(Game, related_name="cards", on_delete=models.CASCADE)
    card = models.ForeignKey("decks.Card", on_delete=models.CASCADE)
    status = models.CharField(max_length=7, default=DECK)
    player = models.ForeignKey(Player, null=True, blank=True, on_delete=models.SET_NULL, related_name="game_cards")
    position = models.IntegerField(default=0)       # orden del mazo mezclado
    slot = models.PositiveSmallIntegerField(null=True, blank=True)  # orden dentro de una jugada

    class Meta:
        indexes = [models.Index(fields=["game", "status"])]