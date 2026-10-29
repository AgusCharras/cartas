from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path("admin/", admin.site.urls),
    path("mazos/", include("decks.urls")),   # path("importar/", import_view, name="import")
    path("", include("game.urls")),
]