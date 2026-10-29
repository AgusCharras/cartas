import uuid

from django import forms
from django.shortcuts import redirect, render

from config.ratelimit import rate_limit
from .importer import DeckImportError, import_deck


class ImportForm(forms.Form):
    file = forms.FileField()


@rate_limit("import", limit=10, window=3600)  # 10 por hora
def import_view(request):
    form = ImportForm(request.POST or None, request.FILES or None)
    error = None
    if request.method == "POST" and form.is_valid():
        token = request.session.setdefault("uploader_token", str(uuid.uuid4()))
        try:
            deck = import_deck(form.cleaned_data["file"].read(), uploader_token=token)
        except DeckImportError as e:
            error = str(e)
        else:
            request.session["my_decks"] = request.session.get("my_decks", []) + [deck.id]
            return redirect("game:home")
    return render(request, "decks/import.html", {"form": form, "error": error})