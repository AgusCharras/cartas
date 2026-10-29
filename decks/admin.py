from django.contrib import admin
from .models import Deck, Card

# Register your models here.

class CardInline(admin.TabularInline):
    model = Card
    extra = 0
    
@admin.register(Deck)
class DeckAdmin(admin.ModelAdmin):
    list_display = ("name", "is_public", "created_at")
    inlines = [CardInline]
    
    