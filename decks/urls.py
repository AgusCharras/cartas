from django.urls import path
from . import views

app_name = "decks"
urlpatterns = [
    path("", views.deck_list, name="list"),
    path("nuevo/", views.deck_new, name="new"),
    path("importar/", views.import_view, name="import"),
    path("c/<str:code>/", views.deck_preview, name="preview"),
    path("<int:pk>/", views.deck_edit, name="edit"),
    path("<int:pk>/exportar/", views.deck_export, name="export"),
    path("<int:pk>/borrar/", views.deck_delete, name="delete"),
]