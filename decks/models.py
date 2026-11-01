import secrets

from django.db import IntegrityError, models, transaction

SHARE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def generate_share_code():
    return "".join(secrets.choice(SHARE_ALPHABET) for _ in range(8))


class Deck(models.Model):
    name = models.CharField(max_length=100)
    description = models.CharField(max_length=300, blank=True)
    is_public = models.BooleanField(default=False)
    # Sin usuarios: el dueño es el token anónimo guardado en su sesión
    uploader_token = models.UUIDField(null=True, blank=True, db_index=True)
    # Compartir: el código solo funciona si is_shared está activo
    is_shared = models.BooleanField(default=False)
    share_code = models.CharField(max_length=8, unique=True, null=True, blank=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name

    def ensure_share_code(self):
        """Genera el código la primera vez que hace falta (los mazos viejos no lo tienen)."""
        if self.share_code:
            return self.share_code
        for _ in range(10):
            self.share_code = generate_share_code()
            try:
                with transaction.atomic():
                    self.save(update_fields=["share_code"])
                return self.share_code
            except IntegrityError:  # código repetido: probamos otro
                continue
        self.share_code = None
        raise RuntimeError("No se pudo generar un código de mazo.")


class Card(models.Model):
    BLACK, WHITE = "black", "white"
    KIND_CHOICES = [(BLACK, "Negra"), (WHITE, "Blanca")]

    deck = models.ForeignKey(Deck, related_name="cards", on_delete=models.CASCADE)
    kind = models.CharField(max_length=5, choices=KIND_CHOICES)
    text = models.CharField(max_length=300)
    pick = models.PositiveSmallIntegerField(default=1)  # solo para negras

    def __str__(self):
        return self.text[:50]