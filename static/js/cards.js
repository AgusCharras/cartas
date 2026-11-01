(function () {
  "use strict";

  const MAX_LEN = 300;     // igual que el servidor
  const MAX_SHOWN = 120;   // cartas que se dibujan en la vista previa (para no trabar el navegador)

  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;  // siempre textContent: nunca innerHTML con texto de usuarios
    return n;
  }

  // Los textos largos se achican para que entren en la carta
  function sizeFor(len, black) {
    if (len > (black ? 180 : 140)) return "s";
    if (len > (black ? 100 : 70)) return "m";
    return "";
  }

  // Escribe el texto de una carta negra dejando cada "_" como un hueco subrayado
  function fillBlanks(card, text) {
    const t = el("span", "t");
    text.split(/_+/).forEach((part, i) => {
      if (i > 0) t.append(el("span", "blank"));
      t.append(document.createTextNode(part));
    });
    card.replaceChildren(t);
  }

  function makeCard(kind, text, brand) {
    const card = el("span", "c " + kind);
    card.dataset.brand = brand;
    const size = sizeFor(text.length, kind === "black");
    if (size) card.dataset.size = size;
    if (kind === "black") fillBlanks(card, text);
    else card.append(el("span", "t", text));
    return card;
  }

  // Mejora las cartas que ya vienen dibujadas por el servidor (marcadas con data-auto)
  function enhance(root) {
    (root || document).querySelectorAll(".c[data-auto]").forEach((card) => {
      const text = card.textContent.trim();
      const black = card.classList.contains("black");
      const size = sizeFor(text.length, black);
      if (size) card.dataset.size = size;
      if (black) fillBlanks(card, text);
      card.removeAttribute("data-auto");
    });
  }

  // Vista previa en vivo: una carta por línea del textarea
  function bindPreview(inputId, outId, kind, countId) {
    const input = document.getElementById(inputId);
    const out = document.getElementById(outId);
    const count = document.getElementById(countId);
    if (!input || !out) return;
    let timer = null;

    function render() {
      out.replaceChildren();
      let total = 0, shown = 0, invalid = 0;

      for (const raw of input.value.split(/\r?\n/)) {
        let text = raw.split(/\s+/).filter(Boolean).join(" ");  // mismo criterio que el servidor
        if (!text) continue;
        total++;

        let error = "";
        let brand = kind === "black" ? "Cartas Arg" : "ARG";
        if (text.length > MAX_LEN) error = `Máx. ${MAX_LEN} caracteres`;
        if (kind === "black") {
          text = text.replace(/_+/g, "_");
          const blanks = (text.match(/_/g) || []).length;
          if (!error && blanks === 0) error = "Falta el hueco _";
          else if (!error && blanks > 3) error = "Máx. 3 huecos";
          else if (!error && blanks > 1) brand = `Cartas Arg · elige ${blanks}`;
        }
        if (error) invalid++;
        if (shown >= MAX_SHOWN) continue;

        const card = makeCard(kind, text, error || brand);
        if (error) card.classList.add("invalid");
        out.append(card);
        shown++;
      }

      if (!total) out.append(el("p", "muted", "Escribí una carta por línea y la ves acá."));
      if (count) {
        let msg = total ? `${total} carta${total === 1 ? "" : "s"}` : "";
        if (total > MAX_SHOWN) msg += ` · se muestran las primeras ${MAX_SHOWN}`;
        if (invalid) msg += ` · ${invalid} con errores`;
        count.textContent = msg;
      }
    }

    input.addEventListener("input", () => {
      clearTimeout(timer);
      timer = setTimeout(render, 120);
    });
    render();
  }

  window.CardsUI = { enhance, bindPreview, makeCard };
})();