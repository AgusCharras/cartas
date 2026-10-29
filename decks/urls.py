from django.urls import path
from . import views

app_name = "decks"
urlpatterns = [
    path("importar/", views.import_view, name="import"),
]