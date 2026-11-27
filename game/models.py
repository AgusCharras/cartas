import secrets
import uuid

from django.db import models
from django.utils import timezone

from decks.models import Deck

# Sin 0/O, 1/I/L para que el código sea fácil de dictar por WhatsApp/Discord
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"

REROLLS_PER_GAME = 2   # cambios de carta por jugador y por partida
GOLDEN_PER_GAME = 1    # cartas doradas (escritas por el jugador) por jugador y por partida
STEALS_PER_GAME = 0    # robos de carta por jugador y por partida (0 = desactivado)


def generate_code():
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(5))


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
    # Ajustes que el host elige en el lobby (se aplican a cada jugador al empezar la partida)
    rerolls_per_player = models.PositiveSmallIntegerField(default=REROLLS_PER_GAME)
    golden_per_player = models.PositiveSmallIntegerField(default=GOLDEN_PER_GAME)
    golden_double = models.BooleanField(default=False)  # la carta dorada ganadora vale 2 puntos
    steals_per_player = models.PositiveSmallIntegerField(default=STEALS_PER_GAME)
    # Votación sin juez: 0 = siempre hay juez, 100 = todas las rondas se votan,
    # en el medio = % de rondas (al azar) en las que votan todos
    vote_chance = models.PositiveSmallIntegerField(default=0)
    # Ronda de descarte: probabilidad (%) de que una ronda sea sorpresa (la negra se revela después de jugar)
    blind_chance = models.PositiveSmallIntegerField(default=0)
    blind_round = models.BooleanField(default=False)  # la ronda actual es de descarte (siempre se decide votando)
    # Miopes: veces por jugador que puede dejar miope (mano borrosa y mezclada) a otro jugador en una ronda
    myopic_per_player = models.PositiveSmallIntegerField(default=0)
    # Versus: probabilidad (%) de que una ronda sea de duelos 1 contra 1 (solo con cantidad par de jugadores)
    versus_chance = models.PositiveSmallIntegerField(default=0)
    versus_round = models.BooleanField(default=False)   # la ronda actual es de duelos
    versus_pairs = models.JSONField(default=list, blank=True)    # [[id_a, id_b], ...] en orden de duelo
    versus_index = models.PositiveSmallIntegerField(default=0)   # duelo que se está votando
    versus_results = models.JSONField(default=list, blank=True)  # duelos ya resueltos de esta ronda
    # Rondas con juez: jugadas (por su token opaco) que el juez ya dio vuelta; el resto sigue boca abajo
    revealed = models.JSONField(default=list, blank=True)
    vote_round = models.BooleanField(default=False)  # la ronda actual se decide votando (no hay juez)
    created_at = models.DateTimeField(auto_now_add=True)
    judge = models.ForeignKey("Player", null=True, blank=True,
                              on_delete=models.SET_NULL, related_name="+")
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
    golden_left = models.PositiveSmallIntegerField(default=GOLDEN_PER_GAME)
    steals_left = models.PositiveSmallIntegerField(default=STEALS_PER_GAME)
    # Mientras alguien le está robando, no puede jugar ni cambiar cartas (el bloqueo vence solo)
    locked_by = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    locked_at = models.DateTimeField(null=True, blank=True)
    robbed_round = models.PositiveSmallIntegerField(default=0)  # ronda en la que le robaron (solo 1 robo por ronda)
    avatar = models.CharField(max_length=8, blank=True)         # personaje (emoji) que eligió; vacío = uno automático
    myopic_left = models.PositiveSmallIntegerField(default=0)   # veces que todavía puede dejar miope a alguien
    myopic_round = models.PositiveSmallIntegerField(default=0)  # ronda en la que lo dejaron miope (0 = ninguna)
    myopic_by = models.CharField(max_length=24, blank=True)     # quién lo dejó miope

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["game", "nickname"], name="unique_nick_per_game")
        ]

    def __str__(self):
        return f"{self.nickname} ({self.game.code})"


class GameCard(models.Model):
    """Una carta dentro de una partida concreta (mazo de robo, mano, jugada o descarte).

    Las cartas doradas no salen de ningún mazo: tienen card=None y el texto en custom_text.
    Existen solo mientras dura la ronda en la que se juegan."""
    DECK, HAND, PLAYED, CURRENT, DISCARD = "deck", "hand", "played", "current", "discard"

    game = models.ForeignKey(Game, related_name="cards", on_delete=models.CASCADE)
    card = models.ForeignKey("decks.Card", null=True, blank=True, on_delete=models.CASCADE)
    custom_text = models.CharField(max_length=300, blank=True)
    status = models.CharField(max_length=7, default=DECK)
    player = models.ForeignKey(Player, null=True, blank=True, on_delete=models.SET_NULL,
                               related_name="game_cards")
    position = models.IntegerField(default=0)       # orden del mazo mezclado
    slot = models.PositiveSmallIntegerField(null=True, blank=True)  # orden dentro de una jugada

    class Meta:
        indexes = [models.Index(fields=["game", "status"])]

    @property
    def text(self):
        return self.card.text if self.card_id else self.custom_text

    @property
    def is_golden(self):
        return self.card_id is None


class Vote(models.Model):
    """Voto de una ronda de votación: quién votó (voter) y a qué jugador pertenece la jugada elegida (target).
    Se borran cuando la ronda termina o se cancela."""
    game = models.ForeignKey(Game, related_name="votes", on_delete=models.CASCADE)
    voter = models.ForeignKey(Player, related_name="votes_cast", on_delete=models.CASCADE)
    target = models.ForeignKey(Player, related_name="votes_received", on_delete=models.CASCADE)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["game", "voter"], name="one_vote_per_player")
        ]