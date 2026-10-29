from channels.generic.websocket import AsyncJsonWebsocketConsumer
from channels.db import database_sync_to_async
from django.core.exceptions import ObjectDoesNotExist

from . import services
from .models import Player
from .services import GameError


class RoomConsumer(AsyncJsonWebsocketConsumer):

    async def connect(self):
        self.code = self.scope["url_route"]["kwargs"]["code"].upper()
        self.group = f"game_{self.code}"

        ids = await self.get_ids()
        if ids is None:
            await self.close(code=4403)  # no pertenece a esta sala
            return
        self.game_id, self.player_id = ids

        await self.channel_layer.group_add(self.group, self.channel_name)
        await self.accept()
        await self.run(services.mark_connected)
        await self.broadcast()

    async def disconnect(self, close_code):
        if hasattr(self, "group"):
            await self.channel_layer.group_discard(self.group, self.channel_name)
        if getattr(self, "player_id", None) is not None:
            await self.run(services.mark_disconnected)
            await self.broadcast()

    async def receive_json(self, content, **kwargs):
        if not isinstance(content, dict):
            return
        action = content.get("action")

        if action == "ping":  # latido: mantiene la presencia al día
            if await self.run(services.heartbeat):
                await self.broadcast()
            return

        try:
            if action == "start_game":
                await self.run(services.start_game)

            elif action == "play_cards":
                ids = content.get("cards")
                if (not isinstance(ids, list) or not 1 <= len(ids) <= 3
                        or not all(type(i) is int for i in ids)):
                    raise GameError("Jugada inválida.")
                await self.run(services.play_cards, ids)

            elif action == "pick_winner":
                sid = content.get("submission")
                if type(sid) is not int:
                    raise GameError("Jugada inválida.")
                await self.run(services.pick_winner, sid)

            elif action == "leave":
                await self.run(services.remove_player, self.player_id)

            elif action == "kick":
                target = content.get("player")
                if type(target) is not int:
                    raise GameError("Jugador inválido.")
                await self.run(services.remove_player, target)

            elif action == "skip_judge":
                await self.run(services.skip_judge)

            elif action == "force_judging":
                await self.run(services.force_judging)

            elif action == "claim_host":
                await self.run(services.claim_host)

            else:
                return
        except GameError as e:
            # El error va solo a quien lo causó
            await self.send_json({"type": "error", "message": str(e)})
            return
        except ObjectDoesNotExist:
            await self.close(code=4404)  # la partida o el jugador ya no existen
            return

        await self.broadcast()

    # Evento de grupo: cada conexión arma SU estado y se lo manda a SU cliente
    async def state_refresh(self, event):
        state = await self.run(services.snapshot)
        if state is None:  # fue expulsado, salió, o la sala se borró
            await self.close(code=4404)
            return
        await self.send_json(state)

    # ---- helpers ----

    async def broadcast(self):
        await self.channel_layer.group_send(self.group, {"type": "state.refresh"})

    def run(self, func, *args):
        return database_sync_to_async(func)(self.game_id, self.player_id, *args)

    @database_sync_to_async
    def get_ids(self):
        token = self.scope["session"].get("player_tokens", {}).get(self.code)
        if not token:
            return None
        player = Player.objects.filter(game__code=self.code, token=token).first()
        return (player.game_id, player.id) if player else None