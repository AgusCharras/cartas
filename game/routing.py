from django.urls import re_path
from . import consumers

websocket_urlpatterns = [
    re_path(r"^ws/sala/(?P<code>[A-Za-z0-9]{4,6})/$", consumers.RoomConsumer.as_asgi()),
]