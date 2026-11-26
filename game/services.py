import hashlib
import hmac
import random
import re
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Count, F, Q
from django.db.models.functions import Greatest
from django.utils import timezone

from decks.models import Card, Deck
from .models import Game, GameCard, Player, Vote

HAND_SIZE = 7
MIN_PLAYERS = 3        # para empezar la partida
MIN_TO_CONTINUE = 2    # si quedan menos, la partida termina
MAX_DECKS_PER_GAME = 8
POINTS_RANGE = (1, 20)    # puntos para ganar
REROLLS_RANGE = (0, 10)   # cambios de carta por jugador
GOLDEN_RANGE = (0, 5)     # cartas doradas por jugador (0 = no hay)
VOTE_RANGE = (0, 100)     # % de rondas que se votan: 0 = siempre juez, 100 = siempre votación
STEALS_RANGE = (0, 5)     # robos de carta por jugador (0 = no se puede robar)
MIN_GOLDEN_LEN = 3      # largo de la carta dorada que escribe el jugador
MAX_GOLDEN_LEN = 140
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


def _pick_of(card):
    """Cuántas respuestas pide una carta negra: una por cada hueco «_» (entre 1 y 3).

    Se calcula del texto y no del campo `pick` de la base, así siempre coincide con lo que
    los jugadores ven en la carta, aunque el mazo se haya cargado con un `pick` incorrecto
    (por ejemplo, importado desde un JSON de texto plano o armado desde el admin)."""
    blanks = len(re.findall(r"_+", card.text))
    return min(max(blanks, 1), 3)


def _sub_token(game, first_card_id):
    """Identificador opaco de una jugada (y a la vez su clave de orden en la mesa).

    Sale de un HMAC con la clave secreta del servidor, la partida, la ronda y la carta:
    es estable durante la ronda (todos ven el mismo orden), cambia de una ronda a otra y
    no revela quién jugó cada una (ni por el orden de entrada ni por el id de la base).
    Son 48 bits para que entre sin perder precisión en un número de JavaScript."""
    msg = f"{game.id}:{game.round_number}:{first_card_id}".encode()
    digest = hmac.new(settings.SECRET_KEY.encode(), msg, hashlib.sha256).hexdigest()
    return int(digest[:12], 16)


def _submissions(game):
    """Jugadas de la ronda: [(token, autor, [cartas])], en el orden aleatorio (estable) de la mesa."""
    by_player = {}
    for gc in game.cards.filter(status=GameCard.PLAYED).select_related("card", "player").order_by("slot"):
        by_player.setdefault(gc.player_id, []).append(gc)
    subs = [(_sub_token(game, cards[0].id), cards[0].player, cards) for cards in by_player.values()]
    subs.sort(key=lambda sub: sub[0])
    return subs


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
    # Los ajustes se eligen en el lobby: recién ahora se reparten a cada jugador
    game.players.update(rerolls_left=game.rerolls_per_player, golden_left=game.golden_per_player,
                        steals_left=game.steals_per_player)
    for p in players:
        _deal(game, p, HAND_SIZE)

    game.judge = random.choice(players)
    _start_round(game)
    game.save()


def _start_round(game):
    """Saca una negra del mazo y abre la ronda. Si no quedan negras, termina la partida."""
    blacks = list(game.cards.filter(status=GameCard.DECK, card__kind=Card.BLACK)
                  .select_related("card").order_by("position"))
    if not blacks:
        game.state = Game.FINISHED
        return
    # Ronda de descarte (sorpresa): se juega sin ver la negra, así que tiene que ser de UNA sola carta
    blind = random.randrange(100) < game.blind_chance
    black = None
    if blind:
        black = next((b for b in blacks if _pick_of(b.card) == 1), None)
        blind = black is not None
    if black is None:
        black = blacks[0]
    black.status = GameCard.CURRENT
    black.save(update_fields=["status"])
    game.round_number += 1
    game.state = Game.PLAYING
    game.players.update(locked_by=None, locked_at=None)
    game.blind_round = blind
    game.revealed = []
    # Ronda de votación: no hay juez, todos juegan y todos votan (la de descarte siempre se vota)
    game.vote_round = blind or random.randrange(100) < game.vote_chance


@transaction.atomic
def play_cards(game_id, player_id, card_ids, golden=None):
    """card_ids: cartas de la mano. golden: (posición, texto) si juega su carta dorada,
    que ocupa una de las posiciones de la jugada (las demás se llenan con la mano, en orden)."""
    game = _lock(game_id)
    if game.state != Game.PLAYING:
        raise GameError("Ahora no se pueden jugar cartas.")
    if not game.vote_round and player_id == game.judge_id:
        raise GameError("Sos el juez de esta ronda: te toca esperar.")
    if game.cards.filter(status=GameCard.PLAYED, player_id=player_id).exists():
        raise GameError("Ya jugaste en esta ronda.")
    me = game.players.filter(pk=player_id).first()
    if me is not None and _lock_active(me):
        raise GameError("Te están robando una carta: esperá un momento.")

    if game.blind_round and golden:
        raise GameError("En la ronda de descarte no se puede usar la carta dorada.")
    pick = _pick_of(_current_black(game).card)
    if len(card_ids) + (1 if golden else 0) != pick or len(set(card_ids)) != len(card_ids):
        raise GameError(f"Tenés que elegir {pick} carta(s) distintas.")

    me = golden_slot = golden_text = None
    if golden:
        golden_slot, golden_text = golden
        golden_text = " ".join(golden_text.split())  # sin saltos de línea ni espacios de más
        if not 0 <= golden_slot < pick:
            raise GameError("Posición de la carta dorada inválida.")
        if not MIN_GOLDEN_LEN <= len(golden_text) <= MAX_GOLDEN_LEN:
            raise GameError(
                f"La carta dorada debe tener entre {MIN_GOLDEN_LEN} y {MAX_GOLDEN_LEN} caracteres.")
        me = game.players.filter(pk=player_id).first()  # se lee DESPUÉS del lock
        if me is None:
            raise GameError("Ya no estás en esta sala.")
        if me.golden_left <= 0:
            raise GameError("Ya usaste tu carta dorada en esta partida." if game.golden_per_player
                            else "En esta partida no hay cartas doradas.")

    hand = {gc.id: gc for gc in game.cards.filter(
        status=GameCard.HAND, player_id=player_id, id__in=card_ids)}
    if len(hand) != len(card_ids):
        raise GameError("Esas cartas no están en tu mano.")

    # La dorada ocupa la posición que eligió el jugador; la mano llena el resto, en orden
    free_slots = [s for s in range(pick) if s != golden_slot]
    for slot, cid in zip(free_slots, card_ids):
        hand[cid].status, hand[cid].slot = GameCard.PLAYED, slot
    GameCard.objects.bulk_update(hand.values(), ["status", "slot"])

    if golden:
        GameCard.objects.create(game=game, card=None, custom_text=golden_text,
                                status=GameCard.PLAYED, player=me, slot=golden_slot)
        me.golden_left -= 1
        me.save(update_fields=["golden_left"])

    submitted = game.cards.filter(status=GameCard.PLAYED).values("player_id").distinct().count()
    needed = game.players.count() - (0 if game.vote_round else 1)  # todos, o todos menos el juez
    if submitted >= needed:
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
    if not game.vote_round and player.id == game.judge_id:
        raise GameError("El juez no necesita cambiar cartas.")
    if game.cards.filter(status=GameCard.PLAYED, player=player).exists():
        raise GameError("Ya jugaste en esta ronda.")
    if _lock_active(player):
        raise GameError("Te están robando una carta: esperá un momento.")
    if game.blind_round:
        raise GameError("En la ronda de descarte no hay cambios: jugá la carta que menos te importe.")
    if player.rerolls_left <= 0:
        raise GameError("No te quedan cambios." if game.rerolls_per_player
                        else "En esta partida no hay cambios de carta.")

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
    if game.vote_round:
        raise GameError("Esta ronda se decide votando entre todos.")
    if player_id != game.judge_id:
        raise GameError("Solo el juez elige la ganadora.")

    first = None
    for gc in game.cards.filter(status=GameCard.PLAYED, slot=0).select_related("player"):
        if _sub_token(game, gc.id) == submission_id:
            first = gc
            break
    if first is None:
        raise GameError("Jugada inválida.")
    if submission_id not in game.revealed:
        raise GameError("Primero dá vuelta esa jugada para leerla.")

    _finish_round(game, first.player)


@transaction.atomic
def reveal_submission(game_id, player_id, submission_id):
    """El juez da vuelta una jugada (boca abajo hasta entonces). Todos ven cómo se va leyendo."""
    game = _lock(game_id)
    if game.state != Game.JUDGING:
        raise GameError("Todavía no hay jugadas para dar vuelta.")
    if game.vote_round:
        raise GameError("En las rondas de votación las jugadas se ven todas juntas.")
    if player_id != game.judge_id:
        raise GameError("Solo el juez da vuelta las cartas.")
    if not any(t == submission_id for t, _a, _c in _submissions(game)):
        raise GameError("Jugada inválida.")
    if submission_id not in game.revealed:
        game.revealed = [*game.revealed, submission_id]
        game.save(update_fields=["revealed"])


def _finish_round(game, winner, extra=None):
    """Suma el punto de la ronda, descarta lo jugado, repone las manos y arranca la siguiente.
    `extra` se agrega al resultado que ven todos (por ejemplo, los votos)."""
    winner_cards = list(game.cards.filter(status=GameCard.PLAYED, player=winner)
                        .order_by("slot").select_related("card"))
    black = _current_black(game)

    # Si el host lo activó, ganar con una carta dorada vale 2 puntos
    gain = 2 if game.golden_double and any(gc.is_golden for gc in winner_cards) else 1
    winner.score += gain
    winner.save(update_fields=["score"])
    game.last_result = {
        "points": gain,
        "round": game.round_number,
        "winner": winner.nickname,
        "black": black.card.text,
        "cards": [gc.text for gc in winner_cards],
        "golden": [gc.is_golden for gc in winner_cards],
        **(extra or {}),
    }

    # Todo lo jugado en la ronda (y la negra) va al descarte
    game.cards.filter(status__in=[GameCard.PLAYED, GameCard.CURRENT]).update(
        status=GameCard.DISCARD, player=None, slot=None)
    game.cards.filter(card__isnull=True, status=GameCard.DISCARD).delete()  # las doradas no se reciclan
    game.votes.all().delete()

    if winner.score >= game.points_to_win:
        game.state = Game.FINISHED
        game.save()
        return

    players = list(game.players.order_by("joined_at", "id"))
    for p in players:  # reponer manos
        missing = HAND_SIZE - game.cards.filter(status=GameCard.HAND, player=p).count()
        if missing > 0:
            _deal(game, p, missing)

    if not game.vote_round:  # el turno de juez solo avanza cuando hubo juez
        idx = next((i for i, p in enumerate(players) if p.id == game.judge_id), -1)
        game.judge = players[(idx + 1) % len(players)]
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
    Las cartas ya jugadas vuelven a la mano de quien las jugó, y las doradas
    gastadas en esta ronda se devuelven (se borran y el jugador recupera su uso)."""
    game.votes.all().delete()
    for gc in list(game.cards.filter(status=GameCard.PLAYED, card__isnull=True)):
        if gc.player_id:
            Player.objects.filter(pk=gc.player_id).update(golden_left=F("golden_left") + 1)
        gc.delete()
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
    game.cards.filter(card__isnull=True, status=GameCard.DISCARD).delete()  # doradas del que se va
    target.delete()

    if game.state in (Game.PLAYING, Game.JUDGING):
        if len(remaining) < MIN_TO_CONTINUE:
            game.state = Game.FINISHED
            if was_judge:
                game.judge = next_judge
        elif was_judge and not game.vote_round:
            _restart_round(game, next_judge)
        else:
            if was_judge:  # ronda de votación: no había juez, solo se corre el turno
                game.judge = next_judge
            if game.vote_round:
                _after_leave_in_vote(game, len(remaining))
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
    if game.vote_round:  # no hay juez que saltear: se repite la ronda y el turno de juez no se mueve
        _restart_round(game, game.judge)
    else:
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
    submitted = game.cards.filter(status=GameCard.PLAYED).values("player_id").distinct().count()
    if game.vote_round and submitted < 2:
        raise GameError("Hacen falta al menos 2 jugadas para poder votar.")
    if submitted < 1:
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


# ---------- votación (rondas sin juez) ----------

@transaction.atomic
def vote(game_id, player_id, token):
    """Vota por una jugada. Se puede cambiar el voto hasta que voten todos. No se puede votar la propia."""
    game = _lock(game_id)
    if game.state != Game.JUDGING or not game.vote_round:
        raise GameError("Ahora no se está votando.")
    me = game.players.filter(pk=player_id).first()  # se lee DESPUÉS del lock
    if me is None:
        raise GameError("Ya no estás en esta sala.")

    target = next((author for t, author, _cards in _submissions(game) if t == token), None)
    if target is None:
        raise GameError("Jugada inválida.")
    if target.id == me.id:
        raise GameError("No podés votar tu propia carta.")

    Vote.objects.update_or_create(game=game, voter=me, defaults={"target": target})
    if game.votes.count() >= game.players.count():  # votaron todos
        _resolve_vote(game)


@transaction.atomic
def close_vote(game_id, player_id):
    """El host cierra la votación sin esperar a los que faltan (por ejemplo, alguien desconectado)."""
    game = _lock(game_id)
    _require_host(game, player_id)
    if game.state != Game.JUDGING or not game.vote_round:
        raise GameError("Ahora no se está votando.")
    if not game.votes.exists():
        raise GameError("Todavía no votó nadie.")
    _resolve_vote(game)


def _resolve_vote(game):
    """Gana la jugada con más votos; si hay empate, se sortea entre las empatadas."""
    counts = {}
    for v in game.votes.all():
        counts[v.target_id] = counts.get(v.target_id, 0) + 1
    top = max(counts.values())
    tied = [pid for pid, n in counts.items() if n == top]
    winner = game.players.get(pk=random.choice(tied))
    _finish_round(game, winner, {"mode": "vote", "votes": top, "tie": len(tied) > 1})


def _after_leave_in_vote(game, n_players):
    """Alguien se fue en una ronda de votación: puede que ya estén todos listos o que falten jugadas."""
    submitted = game.cards.filter(status=GameCard.PLAYED).values("player_id").distinct().count()
    if game.state == Game.PLAYING:
        if submitted >= n_players:  # ya jugaron todos los que quedan
            game.state = Game.JUDGING
    elif game.state == Game.JUDGING:
        if submitted < 2:  # no alcanzan las jugadas para votar: se repite la ronda
            _restart_round(game, game.judge)
        elif game.votes.count() >= n_players:
            _resolve_vote(game)


# ---------- robar cartas (a ciegas) ----------

def _steal_order(game, thief, target):
    """La mano de `target` en un orden opaco: el ladrón elige una posición sin saber qué carta es.

    El orden sale de un HMAC con la clave secreta del servidor, la ronda y la pareja ladrón/víctima:
    es estable mientras la víctima tenga las mismas cartas (si el ladrón reabre el panel ve lo mismo)
    pero no se puede adivinar desde el navegador."""
    cards = list(game.cards.filter(status=GameCard.HAND, player=target).select_related("card"))

    def key(gc):
        msg = f"steal:{game.id}:{game.round_number}:{thief.id}:{target.id}:{gc.id}".encode()
        return hmac.new(settings.SECRET_KEY.encode(), msg, hashlib.sha256).hexdigest()

    cards.sort(key=key)
    return cards


STEAL_LOCK_SECONDS = 20  # tiempo máximo que el ladrón puede tener bloqueada a la víctima


def _lock_active(player, now=None):
    now = now or timezone.now()
    return bool(player.locked_by_id and player.locked_at
                and (now - player.locked_at).total_seconds() < STEAL_LOCK_SECONDS)


def _check_steal(game, player_id, target_id):
    """Validaciones comunes de empezar un robo y de completarlo. Devuelve (ladrón, víctima)."""
    if game.state != Game.PLAYING:
        raise GameError("Solo se puede robar mientras se elige la carta de la ronda.")
    if game.blind_round:
        raise GameError("En la ronda de descarte no se puede robar.")
    thief = game.players.filter(pk=player_id).first()  # se lee DESPUÉS del lock
    if thief is None:
        raise GameError("Ya no estás en esta sala.")
    if thief.steals_left <= 0:
        raise GameError("No te quedan robos." if game.steals_per_player
                        else "En esta partida no se pueden robar cartas.")
    if not game.vote_round and thief.id == game.judge_id:
        raise GameError("Sos el juez de esta ronda: no jugás, así que no podés robar.")
    if game.cards.filter(status=GameCard.PLAYED, player=thief).exists():
        raise GameError("Ya jugaste en esta ronda.")
    if target_id == thief.id:
        raise GameError("No podés robarte a vos mismo.")
    target = game.players.filter(pk=target_id).first()
    if target is None:
        raise GameError("Ese jugador ya no está en la sala.")
    if game.cards.filter(status=GameCard.PLAYED, player=target).exists():
        raise GameError(f"{target.nickname} ya jugó: no se le puede robar.")
    if target.robbed_round == game.round_number:
        # Un solo robo por víctima y por ronda: si no, varios ladrones la dejarían sin cartas para jugar
        raise GameError(f"A {target.nickname} ya le robaron una carta en esta ronda.")
    return thief, target


@transaction.atomic
def steal_start(game_id, player_id, target_id):
    """El ladrón toca a la víctima: desde acá la víctima no puede jugar (y ve el aviso) hasta que
    el robo se completa, se cancela o vence el tiempo. Devuelve los datos para avisar a la sala."""
    game = _lock(game_id)
    thief, target = _check_steal(game, player_id, target_id)
    now = timezone.now()
    if target.locked_by_id not in (None, thief.id) and _lock_active(target, now):
        raise GameError(f"A {target.nickname} ya la están robando.")
    if not game.players.filter(pk=target.id).exists():
        raise GameError("Ese jugador ya no está en la sala.")
    if not game.cards.filter(status=GameCard.HAND, player=target).exists():
        raise GameError(f"{target.nickname} no tiene cartas para robar.")
    # Un ladrón bloquea a una sola víctima a la vez
    game.players.filter(locked_by=thief).exclude(pk=target.id).update(locked_by=None, locked_at=None)
    target.locked_by, target.locked_at = thief, now
    target.save(update_fields=["locked_by", "locked_at"])
    return {"thief": thief.nickname, "victim": target.nickname}


@transaction.atomic
def steal_cancel(game_id, player_id):
    game = _lock(game_id)
    game.players.filter(locked_by_id=player_id).update(locked_by=None, locked_at=None)


@transaction.atomic
def steal_card(game_id, player_id, target_id, slot):
    """Completa el robo: toma la carta que está en la posición `slot` (boca abajo) de la víctima.
    Hay que haber empezado el robo antes (steal_start) y que el bloqueo siga vigente."""
    game = _lock(game_id)
    thief, target = _check_steal(game, player_id, target_id)
    if target.locked_by_id != thief.id or not _lock_active(target):
        raise GameError("Se te acabó el tiempo para robar: volvé a intentarlo.")

    hand = _steal_order(game, thief, target)
    if not hand:
        raise GameError(f"{target.nickname} no tiene cartas para robar.")
    if type(slot) is not int or not 0 <= slot < len(hand):
        raise GameError("Carta inválida.")

    stolen = hand[slot]
    stolen.player = thief
    stolen.save(update_fields=["player"])
    thief.steals_left -= 1
    thief.save(update_fields=["steals_left"])
    target.robbed_round = game.round_number
    target.locked_by, target.locked_at = None, None
    target.save(update_fields=["robbed_round", "locked_by", "locked_at"])
    return {"thief_id": thief.id, "thief": thief.nickname, "victim_id": target.id, "victim": target.nickname,
            "card_id": stolen.id, "card_text": stolen.text}


# ---------- ajustes de la partida (solo en el lobby) ----------

@transaction.atomic
def update_settings(game_id, player_id, values):
    """values: dict con alguna de estas claves: points_to_win, rerolls, golden, golden_double."""
    game = _lock(game_id)
    _require_host(game, player_id)
    if game.state != Game.LOBBY:
        raise GameError("Los ajustes solo se pueden cambiar antes de empezar.")

    def in_range(name, value, bounds):
        low, high = bounds
        if type(value) is not int or not low <= value <= high:
            raise GameError(f"{name}: tiene que estar entre {low} y {high}.")
        return value

    if "points_to_win" in values:
        game.points_to_win = in_range("Puntos para ganar", values["points_to_win"], POINTS_RANGE)
    if "rerolls" in values:
        game.rerolls_per_player = in_range("Cambios de carta", values["rerolls"], REROLLS_RANGE)
    if "golden" in values:
        game.golden_per_player = in_range("Cartas doradas", values["golden"], GOLDEN_RANGE)
    if "steals" in values:
        game.steals_per_player = in_range("Robos de carta", values["steals"], STEALS_RANGE)
    if "blind_chance" in values:
        game.blind_chance = in_range("Probabilidad de ronda de descarte", values["blind_chance"], VOTE_RANGE)
    if "vote_chance" in values:
        game.vote_chance = in_range("Probabilidad de votación", values["vote_chance"], VOTE_RANGE)
    if "golden_double" in values:
        if type(values["golden_double"]) is not bool:
            raise GameError("Ajuste inválido.")
        game.golden_double = values["golden_double"]
    game.save()


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
        # Ronda de descarte: el texto NO sale del servidor hasta que todos jugaron (si no, se podría leer del socket)
        secret = game.blind_round and game.state == Game.PLAYING
        black = {"text": None if secret else card.text, "pick": _pick_of(card), "secret": secret}

    hand = []
    if in_round:
        hand = [{"id": gc.id, "text": gc.text}
                for gc in game.cards.filter(status=GameCard.HAND, player_id=player_id)
                .select_related("card").order_by("id")]

    # Cuántas cartas tiene cada uno en la mano (se muestran boca abajo al robar; nunca cuáles son)
    hand_counts = {}
    if in_round:
        for pid in game.cards.filter(status=GameCard.HAND).values_list("player_id", flat=True):
            hand_counts[pid] = hand_counts.get(pid, 0) + 1

    # En una ronda de votación no hay juez
    judge_id = None if game.vote_round else game.judge_id

    submissions = []
    my_vote = None
    voted_ids = set()
    if game.state == Game.JUDGING:
        subs = _submissions(game)
        # El id es un token opaco y el orden sale del mismo token: ni uno ni otro delatan
        # quién jugó cada respuesta (la posición en el mazo sí seguía el orden de los jugadores).
        for token, author, cards in subs:
            # Ojo: NO se informa cuáles son doradas. Aunque el navegador no las pinte distinto,
            # el dato viajaría en el mensaje y se podría ver en las herramientas de desarrollo.
            # Recién se revela en last_result, cuando ya se eligió.
            # Con juez, cada jugada viaja boca abajo (sin texto) hasta que el juez la da vuelta
            if game.vote_round or token in game.revealed:
                sub = {"id": token, "cards": [c.text for c in cards], "revealed": True}
            else:
                sub = {"id": token, "n": len(cards), "revealed": False}
            if game.vote_round and author.id == me.id:
                sub["mine"] = True  # solo se lo informamos a su autora: no puede votarla
            submissions.append(sub)
        if game.vote_round:
            votes = list(game.votes.all())
            voted_ids = {v.voter_id for v in votes}
            mine = next((v for v in votes if v.voter_id == me.id), None)
            if mine:
                my_vote = next((t for t, author, _c in subs if author.id == mine.target_id), None)

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
        "rerolls_per_player": game.rerolls_per_player,
        "golden_per_player": game.golden_per_player,
        "golden_double": game.golden_double,
        "steals_per_player": game.steals_per_player,
        "vote_chance": game.vote_chance,
        "vote_round": game.vote_round,
        "blind_chance": game.blind_chance,
        "blind_round": game.blind_round,
        "votes_cast": len(voted_ids),
        "voters_total": len(players),
        "min_players": MIN_PLAYERS,
        "me": {
            "id": me.id,
            "nickname": me.nickname,
            "is_host": me.is_host,
            "is_judge": me.id == judge_id,
            "has_played": me.id in played_ids,
            "has_voted": me.id in voted_ids,
            "vote": my_vote,
            "rerolls_left": me.rerolls_left,
            "golden_left": me.golden_left,
            "steals_left": me.steals_left,
            # Si alguien me está robando ahora: quién y cuántos segundos queda bloqueado
            "robbed_by": (next((p.nickname for p in players if p.id == me.locked_by_id), None)
                          if game.state == Game.PLAYING and _lock_active(me, now) else None),
            "lock_left": (max(0, round(STEAL_LOCK_SECONDS - (now - me.locked_at).total_seconds()))
                          if game.state == Game.PLAYING and _lock_active(me, now) else 0),
            "hand": hand,
        },
        "players": [
            {"id": p.id, "nickname": p.nickname, "is_host": p.is_host, "score": p.score,
             "is_judge": p.id == judge_id, "has_played": p.id in played_ids,
             "has_voted": p.id in voted_ids,
             "hand_count": hand_counts.get(p.id, 0),
             "robbed": game.round_number > 0 and p.robbed_round == game.round_number,
             "being_robbed": game.state == Game.PLAYING and _lock_active(p, now),
             "robbing_me": game.state == Game.PLAYING and _lock_active(p, now) and p.locked_by_id == me.id,
             "online": is_online(p, now)}
            for p in players
        ],
        "black": black,
        "submissions": submissions,
        "decks": decks,
        "needed_whites": len(players) * (HAND_SIZE + 1),
        "last_result": game.last_result,
    }