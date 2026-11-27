/* Vista previa de un mazo "barajando": muestra una carta (negra o blanca) unos segundos y pasa a la siguiente.
   Uso:  DeckShuffle.mount(elemento, [{kind: "black"|"white", text: "...", pick: 1}, ...], {interval: 5500})
   Necesita cards.js (CardsUI.makeCard). Con "reducir movimiento" activado en el sistema, solo cambia la carta. */
(function () {
  "use strict";

  const reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function shuffled(list) {
    const a = list.slice();
    for (let i = a.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [a[i], a[j]] = [a[j], a[i]];
    }
    return a;
  }

  // Orden de aparición: mezcla las negras y las blancas para que se vean los dos tipos, sin repetir carta seguida
  function playlist(cards) {
    const blacks = shuffled(cards.filter((c) => c.kind === "black"));
    const whites = shuffled(cards.filter((c) => c.kind !== "black"));
    const out = [];
    let b = 0, w = 0;
    while (b < blacks.length || w < whites.length) {
      if (b < blacks.length) out.push(blacks[b++]);                       // una negra...
      for (let k = 0; k < 2 && w < whites.length; k++) out.push(whites[w++]);  // ...y dos blancas
    }
    return out;
  }

  function brandFor(c) {
    if (c.kind !== "black") return "ARG";
    return c.pick > 1 ? "Cartas Arg · elige " + c.pick : "Cartas Arg";
  }

  function mount(root, cards, opts) {
    opts = opts || {};
    const interval = opts.interval || 5500;
    root.classList.add("deckshuffle");
    root.replaceChildren();
    if (!cards || !cards.length) {
      const empty = document.createElement("div");
      empty.className = "ds-empty";
      empty.textContent = "Sin cartas";
      root.append(empty);
      return { stop() {} };
    }

    for (const cls of ["ds-back b2", "ds-back b1"]) {  // las cartas de abajo del mazo
      const back = document.createElement("span");
      back.className = cls;
      root.append(back);
    }
    const slot = document.createElement("div");
    slot.className = "ds-slot";
    root.append(slot);

    const list = playlist(cards);
    let i = 0, current = null, timer = null, paused = false, busy = false;

    function build(c) {
      const node = window.CardsUI.makeCard(c.kind === "black" ? "black" : "white", c.text, brandFor(c));
      node.classList.add("ds-card");
      return node;
    }

    function show(index, first) {
      const next = build(list[index % list.length]);
      const old = current;
      current = next;
      slot.append(next);
      if (reduce || first) {
        if (old) old.remove();
        if (!reduce && first) next.classList.add("ds-in");
        return;
      }
      busy = true;
      old.classList.add("ds-out");
      next.classList.add("ds-in");
      setTimeout(() => { old.remove(); busy = false; }, 650);
    }

    function step() {
      if (paused || busy || document.hidden) return;
      i += 1;
      show(i, false);
    }

    show(0, true);
    if (list.length > 1) timer = setInterval(step, interval);
    root.addEventListener("mouseenter", () => { paused = true; });
    root.addEventListener("mouseleave", () => { paused = false; });
    root.addEventListener("click", () => { if (!busy && list.length > 1) { i += 1; show(i, false); } });  // un toque pasa a la siguiente
    return { stop() { clearInterval(timer); } };
  }

  // Monta todos los elementos .deckshuffle[data-cards="id-del-script-json"]
  function auto(root) {
    (root || document).querySelectorAll(".deckshuffle[data-cards]").forEach((el, n) => {
      const src = document.getElementById(el.dataset.cards);
      if (!src) return;
      let cards = [];
      try { cards = JSON.parse(src.textContent); } catch (e) { /* sin datos */ }
      // arrancan desfasados para que no cambien todas a la vez
      setTimeout(() => mount(el, cards, { interval: 5500 }), n * 450);
    });
  }

  window.DeckShuffle = { mount, auto };
})();