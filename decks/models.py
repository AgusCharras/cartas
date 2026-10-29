from django.db import models

import uuid
from django.db import models


class Deck(models.Model):
    name = models.CharField(max_length=100)
    description = models.CharField(max_length=300, blank=True)
    is_public = models.BooleanField(default=False)
    # Sin usuarios: guardamos quién lo subió con un token anónimo (de la sesión)
    uploader_token = models.UUIDField(null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class Card(models.Model):
    BLACK, WHITE = "black", "white"
    KIND_CHOICES = [(BLACK, "Negra"), (WHITE, "Blanca")]

    deck = models.ForeignKey(Deck, related_name="cards", on_delete=models.CASCADE)
    kind = models.CharField(max_length=5, choices=KIND_CHOICES)
    text = models.CharField(max_length=300)
    pick = models.PositiveSmallIntegerField(default=1)  # solo para negras

    def __str__(self):
        return self.text[:50]