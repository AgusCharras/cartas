from .models import Player


def get_player(request, game):
    """Devuelve el Player de esta sesión en esta sala, o None."""
    tokens = request.session.get("player_tokens", {})
    token = tokens.get(game.code)
    if not token:
        return None
    return Player.objects.filter(game=game, token=token).first()


def remember_player(request, player):
    tokens = request.session.get("player_tokens", {})
    tokens[player.game.code] = str(player.token)
    request.session["player_tokens"] = tokens
    request.session.modified = True