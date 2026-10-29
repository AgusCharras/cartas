from django.urls import path
from . import views

app_name = "game"
urlpatterns = [
    path("", views.home, name="home"),
    path("sala/<str:code>/", views.join, name="join"),
    path("sala/<str:code>/jugar/", views.room, name="room"),
]