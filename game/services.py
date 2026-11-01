import random
from datetime import timedelta

from django.db import transaction
from django.db.models import Count, F, Q
from django.db.models.functions import Greatest
from django.utils import timezone

from decks.models import Card, Deck
from .models import Game, GameCard, Player

HAND_SIZE = 7
MIN_PLAYERS = 3        # para empezar la partida
MIN_TO_CONTINUE = 2    # si quedan menos, la partida termina
MAX_DECKS_PER_GAME = 8
GRACE = timedelta(seconds=20)   # cuánto debe llevar desconectado el host para poder tomarle el rol
STALE = timedelta(seconds=90)   # sin latidos por este tiempo = se lo considera desconectado


class GameError(Exception):
    """Error esperable (jugada inválida, no es tu turno...). Se le muestra al jugador."""


def _lock(game_id):
    # select_for_update evita que dos jugadas simultáneas se pisen (en PostgreSQL bloquea la fila;
    # SQLite ya serializa las escrituras por sí mismo).
    return Game.objects.select_for_update().get(pk=game_id)


def _current_black(game):
    return game.cards.select_related("card").get(status=GameCard.CURRENT)


# ---------- mazos ----------

def _recycle(game, kind):
    """Mezcla el descarte y lo vuelve a poner en el mazo."""
    discarded = list(game.cards.filter(status=GameCard.DISCARD, card__kind=kind))
    random.shuffle(discarded)
    last = game.cards.order_by("-position").values_list("position", flat=True).first() or 0
    for i, gc in enumerate(discarded, start=1):
        gc.status, gc.player, gc.slot, gc.position = GameCard.DECK, None, None, last + i
    GameCard.objects.bulk_update(discarded, ["status", "player", "slot", "position"])


def _draw(game, kind, n):
    def top():
        return list(game.cards.filter(status=GameCard.DECK, card__kind=kind).order_by("position")[:n])

    cards = top()
    if len(cards) < n:
        _recycle(game, kind)
        cards = top()
    if len(cards) < n:
        raise GameError("Se acabaron las cartas del mazo.")
    return cards


def _deal(game, player, n):
    drawn = _draw(game, Card.WHITE, n)
    for gc in drawn:
        gc.status, gc.player = GameCard.HAND, player
    GameCard.objects.bulk_update(drawn, ["status", "player"])


# ---------- acciones de la partida ----------

@transaction.atomic
def start_game(game_id, player_id):
    game = _lock(game_id)
    player = game.players.get(pk=player_id)
    if not player.is_host:
        raise GameError("Solo el host puede empezar la partida.")
    if game.state != Game.LOBBY:
        raise GameError("La partida ya empezó.")

    players = list(game.players.order_by("joined_at", "id"))
    if len(players) < MIN_PLAYERS:
        raise GameError(f"Se necesitan al menos {MIN_PLAYERS} jugadores.")

    cards = list(Card.objects.filter(deck__in=game.decks.all()))
    whites = sum(1 for c in cards if c.kind == Card.WHITE)
    blacks = sum(1 for c in cards if c.kind == Card.BLACK)
    if not blacks:
        raise GameError("La sala no tiene cartas negras (¿elegiste algún mazo?).")
    if whites < len(players) * HAND_SIZE + len(players):
        raise GameError("No hay suficientes cartas blancas para esta cantidad de jugadores.")

    random.shuffle(cards)
    GameCard.objects.bulk_create(
        [GameCard(game=game, card=c, position=i) for i, c in enumerate(cards)]
    )
    for p in players:
        _deal(game, p, HAND_SIZE)

    game.judge = random.choice(players)
    _start_round(game)
    game.save()


def _start_round(game):
    """Saca una negra del mazo y abre la ronda. Si no quedan negras, termina la partida."""
    black = (game.cards.filter(status=GameCard.DECK, card__kind=Card.BLACK)
             .order_by("position").first())
    if black is None:
        game.state = Game.FINISHED
        return
    black.status = GameCard.CURRENT
    black.save(update_fields=["status"])
    game.round_number += 1
    game.state = Game.PLAYING


@transaction.atomic
def play_cards(game_id, player_id, card_ids):
    game = _lock(game_id)
    if game.state != Game.PLAYING:
        raise GameError("Ahora no se pueden jugar cartas.")
    if player_id == game.judge_id:
        raise GameError("Sos el juez de esta ronda: te toca esperar.")
    if game.cards.filter(status=GameCard.PLAYED, player_id=player_id).exists():
        raise GameError("Ya jugaste en esta ronda.")

    pick = _current_black(game).card.pick
    if len(card_ids) != pick or len(set(card_ids)) != pick:
        raise GameError(f"Tenés que elegir {pick} carta(s) distintas.")

    hand = {gc.id: gc for gc in game.cards.filter(
        status=GameCard.HAND, player_id=player_id, id__in=card_ids)}
    if len(hand) != pick:
        raise GameError("Esas cartas no están en tu mano.")

    for slot, cid in enumerate(card_ids):
        hand[cid].status, hand[cid].slot = GameCard.PLAYED, slot
    GameCard.objects.bulk_update(hand.values(), ["status", "slot"])

    submitted = game.cards.filter(status=GameCard.PLAYED).values("player_id").distinct().count()
    if submitted >= game.players.count() - 1:  # todos menos el juez
        game.state = Game.JUDGING
        game.save(update_fields=["state"])


@transaction.atomic
def reroll_card(game_id, player_id, card_id):
    game = _lock(game_id)  # el lock evita que dos clics rápidos gasten dos cambios
    if game.state != Game.PLAYING:
        raise GameError("Solo podés cambiar cartas mientras se juega la ronda.")

    player = game.players.filter(pk=player_id).first()  # se lee DESPUÉS del lock
    if player is None:
        raise GameError("Ya no estás en esta sala.")
    if player.id == game.judge_id:
        raise GameError("El juez no necesita cambiar cartas.")
    if game.cards.filter(status=GameCard.PLAYED, player=player).exists():
        raise GameError("Ya jugaste en esta ronda.")
    if player.rerolls_left <= 0:
        raise GameError("No te quedan cambios.")

    old = game.cards.filter(pk=card_id, status=GameCard.HAND, player=player).first()
    if old is None:
        raise GameError("Esa carta no está en tu mano.")

    # Primero se roba y después se descarta: así nunca te vuelve la misma carta
    new = _draw(game, Card.WHITE, 1)[0]
    new.status, new.player = GameCard.HAND, player
    new.save(update_fields=["status", "player"])

    old.status, old.player, old.slot = GameCard.DISCARD, None, None
    old.save(update_fields=["status", "player", "slot"])

    player.rerolls_left -= 1
    player.save(update_fields=["rerolls_left"])


@transaction.atomic
def pick_winner(game_id, player_id, submission_id):
    game = _lock(game_id)
    if game.state != Game.JUDGING:
        raise GameError("Todavía no es momento de elegir.")
    if player_id != game.judge_id:
        raise GameError("Solo el juez elige la ganadora.")

    first = (game.cards.filter(id=submission_id, status=GameCard.PLAYED, slot=0)
             .select_related("player").first())
    if first is None:
        raise GameError("Jugada inválida.")

    winner = first.player
    winner_cards = list(game.cards.filter(status=GameCard.PLAYED, player=winner)
                        .order_by("slot").select_related("card"))
    black = _current_black(game)

    winner.score += 1
    winner.save(update_fields=["score"])
    game.last_result = {
        "round": game.round_number,
        "winner": winner.nickname,
        "black": black.card.text,
        "cards": [gc.card.text for gc in winner_cards],
    }

    # Todo lo jugado en la ronda (y la negra) va al descarte
    game.cards.filter(status__in=[GameCard.PLAYED, GameCard.CURRENT]).update(
        status=GameCard.DISCARD, player=None, slot=None)

    if winner.score >= game.points_to_win:
        game.state = Game.FINISHED
        game.save()
        return

    players = list(game.players.order_by("joined_at", "id"))
    for p in players:  # reponer manos
        missing = HAND_SIZE - game.cards.filter(status=GameCard.HAND, player=p).count()
        if missing > 0:
            _deal(game, p, missing)

    idx = next(i for i, p in enumerate(players) if p.id == game.judge_id)
    game.judge = players[(idx + 1) % len(players)]  # rota el juez
    _start_round(game)
    game.save()


# ---------- presencia ----------

def is_online(player, now=None):
    now = now or timezone.now()
    return player.connections > 0 and now - player.last_seen < STALE


def mark_connected(game_id, player_id):
    Player.objects.filter(pk=player_id).update(
        connections=F("connections") + 1, last_seen=timezone.now())


def mark_disconnected(game_id, player_id):
    Player.objects.filter(pk=player_id).update(
        connections=Greatest(F("connections") - 1, 0), last_seen=timezone.now())


def heartbeat(game_id, player_id):
    """Latido del cliente. Devuelve True si cambió la presencia de alguien (hay que refrescar a todos)."""
    now = timezone.now()
    me = Player.objects.filter(pk=player_id).first()
    if me is None:
        return False
    changed = not is_online(me, now)  # estaba marcado como offline y sigue vivo
    Player.objects.filter(pk=player_id).update(
        last_seen=now, connections=Greatest(F("connections"), 1))
    # Autocorrección: sockets que quedaron "abiertos" tras una caída del servidor
    stale = Player.objects.filter(
        game_id=game_id, connections__gt=0, last_seen__lt=now - STALE).update(connections=0)
    return changed or stale > 0


# ---------- gestión de jugadores ----------

def _require_host(game, player_id):
    p = game.players.filter(pk=player_id).first()
    if p is None:
        raise GameError("Ya no estás en esta sala.")
    if not p.is_host:
        raise GameError("Solo el host puede hacer eso.")
    return p


def _restart_round(game, new_judge):
    """Cancela la ronda en curso y arranca otra con otra negra y otro juez.
    Las cartas ya jugadas vuelven a la mano de quien las jugó."""
    game.cards.filter(status=GameCard.PLAYED, player__isnull=False).update(
        status=GameCard.HAND, slot=None)
    game.cards.filter(status__in=[GameCard.PLAYED, GameCard.CURRENT]).update(
        status=GameCard.DISCARD, player=None, slot=None)
    game.judge = new_judge
    game.round_number -= 1  # la ronda cancelada no cuenta
    _start_round(game)


@transaction.atomic
def remove_player(game_id, actor_id, target_id):
    """Sirve para 'salir' (actor == target) y para que el host expulse."""
    game = _lock(game_id)
    players = list(game.players.order_by("joined_at", "id"))
    actor = next((p for p in players if p.id == actor_id), None)
    target = next((p for p in players if p.id == target_id), None)
    if actor is None:
        raise GameError("Ya no estás en esta sala.")
    if target is None:
        raise GameError("Ese jugador ya no está en la sala.")
    if actor_id != target_id and not actor.is_host:
        raise GameError("Solo el host puede expulsar jugadores.")

    remaining = [p for p in players if p.id != target_id]
    if not remaining:
        game.delete()  # se fue el último
        return

    now = timezone.now()
    if target.is_host:  # traspaso automático: prioriza a alguien conectado
        new_host = next((p for p in remaining if is_online(p, now)), remaining[0])
        new_host.is_host = True
        new_host.save(update_fields=["is_host"])

    next_judge = players[(players.index(target) + 1) % len(players)]
    was_judge = game.judge_id == target_id

    # Sus cartas (mano y jugadas) van al descarte
    game.cards.filter(player_id=target_id).update(
        status=GameCard.DISCARD, player=None, slot=None)
    target.delete()

    if game.state in (Game.PLAYING, Game.JUDGING):
        if len(remaining) < MIN_TO_CONTINUE:
            game.state = Game.FINISHED
            if was_judge:
                game.judge = next_judge
        elif was_judge:
            _restart_round(game, next_judge)
        elif game.state == Game.PLAYING:
            # Quizás el que se fue era el único que faltaba jugar
            submitted = (game.cards.filter(status=GameCard.PLAYED)
                         .values("player_id").distinct().count())
            if submitted >= len(remaining) - 1:
                game.state = Game.JUDGING
    elif was_judge:
        game.judge = next_judge
    game.save()


@transaction.atomic
def skip_judge(game_id, player_id):
    game = _lock(game_id)
    _require_host(game, player_id)
    if game.state not in (Game.PLAYING, Game.JUDGING):
        raise GameError("No hay una ronda en curso.")
    players = list(game.players.order_by("joined_at", "id"))
    idx = next(i for i, p in enumerate(players) if p.id == game.judge_id)
    _restart_round(game, players[(idx + 1) % len(players)])
    game.save()


@transaction.atomic
def force_judging(game_id, player_id):
    """El host decide seguir sin los que todavía no jugaron."""
    game = _lock(game_id)
    _require_host(game, player_id)
    if game.state != Game.PLAYING:
        raise GameError("No hay una ronda esperando jugadas.")
    if not game.cards.filter(status=GameCard.PLAYED).exists():
        raise GameError("Todavía no jugó nadie.")
    game.state = Game.JUDGING
    game.save(update_fields=["state"])


@transaction.atomic
def claim_host(game_id, player_id):
    game = _lock(game_id)
    me = game.players.filter(pk=player_id).first()
    if me is None:
        raise GameError("Ya no estás en esta sala.")
    if me.is_host:
        raise GameError("Ya sos el host.")
    now = timezone.now()
    host = game.players.filter(is_host=True).first()
    if host and (is_online(host, now) or now - host.last_seen < GRACE):
        raise GameError("El host se desconectó hace muy poco. Esperá unos segundos.")
    game.players.filter(is_host=True).update(is_host=False)
    me.is_host = True
    me.save(update_fields=["is_host"])


# ---------- mazos de la sala (solo en el lobby) ----------

def _can_use_deck(deck, token, by_code):
    if deck.is_public:
        return True
    if token and deck.uploader_token and str(deck.uploader_token) == str(token):
        return True
    return by_code and deck.is_shared


@transaction.atomic
def add_deck(game_id, player_id, deck_id, code, token):
    game = _lock(game_id)
    _require_host(game, player_id)
    if game.state != Game.LOBBY:
        raise GameError("Los mazos solo se pueden cambiar antes de empezar.")

    if deck_id is not None:
        deck = Deck.objects.filter(pk=deck_id).first()
    else:
        deck = Deck.objects.filter(share_code=code.strip().upper()).first()
    # Mismo mensaje si no existe o no tenés permiso: no revela mazos privados
    if deck is None or not _can_use_deck(deck, token, by_code=deck_id is None):
        raise GameError("No encontré ese mazo. Revisá el código.")
    if game.decks.filter(pk=deck.pk).exists():
        raise GameError("Ese mazo ya está en la sala.")
    if game.decks.count() >= MAX_DECKS_PER_GAME:
        raise GameError(f"Máximo {MAX_DECKS_PER_GAME} mazos por sala.")
    game.decks.add(deck)


@transaction.atomic
def remove_deck(game_id, player_id, deck_id):
    game = _lock(game_id)
    _require_host(game, player_id)
    if game.state != Game.LOBBY:
        raise GameError("Los mazos solo se pueden cambiar antes de empezar.")
    game.decks.remove(deck_id)


# ---------- chat ----------

def chat_author(game_id, player_id):
    """Apodo del jugador para el chat (None si ya no está en la sala)."""
    return (Player.objects.filter(pk=player_id, game_id=game_id)
            .values_list("nickname", flat=True).first())


# ---------- lo que ve cada jugador ----------

def snapshot(game_id, player_id):
    """Estado PERSONALIZADO para un jugador. Devuelve None si ya no pertenece a la partida.
    Nunca incluye tokens ni manos ajenas."""
    game = Game.objects.filter(pk=game_id).first()
    if game is None:
        return None
    players = list(game.players.order_by("joined_at", "id"))
    me = next((p for p in players if p.id == player_id), None)
    if me is None:
        return None

    now = timezone.now()
    in_round = game.state in (Game.PLAYING, Game.JUDGING)
    played_ids = set(game.cards.filter(status=GameCard.PLAYED).values_list("player_id", flat=True))

    black = None
    if in_round:
        card = _current_black(game).card
        black = {"text": card.text, "pick": card.pick}

    hand = []
    if in_round:
        hand = [{"id": gc.id, "text": gc.card.text}
                for gc in game.cards.filter(status=GameCard.HAND, player_id=player_id)
                .select_related("card").order_by("id")]

    submissions = []
    if game.state == Game.JUDGING:
        by_player = {}
        for gc in game.cards.filter(status=GameCard.PLAYED).select_related("card").order_by("slot"):
            by_player.setdefault(gc.player_id, []).append(gc)
        # El id de la jugada es el de su primera carta: no revela quién la jugó.
        # Se ordenan por 'position' (aleatoria), así el orden no delata a nadie.
        for cards in sorted(by_player.values(), key=lambda cs: cs[0].position):
            submissions.append({"id": cards[0].id, "cards": [c.card.text for c in cards]})

    decks = []
    if game.state == Game.LOBBY:
        decks = [
            {"id": d.id, "name": d.name, "whites": d.n_white, "blacks": d.n_black}
            for d in game.decks.annotate(
                n_white=Count("cards", filter=Q(cards__kind=Card.WHITE)),
                n_black=Count("cards", filter=Q(cards__kind=Card.BLACK)),
            ).order_by("name")
        ]

    return {
        "type": "state",
        "state": game.state,
        "round": game.round_number,
        "points_to_win": game.points_to_win,
        "min_players": MIN_PLAYERS,
        "me": {
            "id": me.id,
            "nickname": me.nickname,
            "is_host": me.is_host,
            "is_judge": me.id == game.judge_id,
            "has_played": me.id in played_ids,
            "rerolls_left": me.rerolls_left,
            "hand": hand,
        },
        "players": [
            {"id": p.id, "nickname": p.nickname, "is_host": p.is_host, "score": p.score,
             "is_judge": p.id == game.judge_id, "has_played": p.id in played_ids,
             "online": is_online(p, now)}
            for p in players
        ],
        "black": black,
        "submissions": submissions,
        "decks": decks,
        "needed_whites": len(players) * (HAND_SIZE + 1),
        "last_result": game.last_result,
    }