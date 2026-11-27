/* Pantalla de apodo: agrega arriba del campo un selector de personaje (emoji), al estilo de Gartic Phone.
   Se engancha solo a cualquier formulario que tenga un campo de apodo; la elección queda guardada en este
   navegador y la sala se la manda al servidor al conectarse. */
(function () {
  var A = window.CARTAS_AVATARS;
  if (!A) return;
  // Solo el campo del apodo (no "name": los formularios de mazos usan ese nombre) y nunca en formularios de edición
  var inp = document.querySelector(
    'form input[name="nickname"], form input[name="nick"], form input[name="apodo"], form[data-namepicker] input[type="text"]');
  if (!inp) return;
  var form = inp.closest("form");
  if (form.querySelector('input[name="action"], textarea')) return;
  document.body.classList.add("namepage");
  if (inp.id) form.querySelectorAll('label[for="' + inp.id + '"]').forEach(function (l) { l.hidden = true; });
  form.classList.add("namecard");

  var cur = window.cartasAvatarGet();
  if (cur === null) cur = A[Math.floor(Math.random() * A.length)];
  var idx = A.indexOf(cur);

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  }

  var hidden = el("input");
  hidden.type = "hidden";
  hidden.name = "avatar";
  form.append(hidden);

  var box = el("div", "namepick");
  box.append(el("div", "np-title", "ELEGÍ UN PERSONAJE Y UN APODO"));

  var row = el("div", "np-row");
  var prev = el("button", "np-arrow", "◀"); prev.type = "button"; prev.setAttribute("aria-label", "Personaje anterior");
  var next = el("button", "np-arrow", "▶"); next.type = "button"; next.setAttribute("aria-label", "Personaje siguiente");
  var wrap = el("div", "np-avatar-wrap");
  var face = el("button", "np-avatar"); face.type = "button"; face.title = "Ver todos los personajes";
  var shuffle = el("button", "np-shuffle", "🔄"); shuffle.type = "button"; shuffle.title = "Al azar";
  wrap.append(face, shuffle);
  row.append(prev, wrap, next);
  box.append(row);

  var grid = el("div", "np-grid");
  grid.hidden = true;
  var cells = A.map(function (e, i) {
    var b = el("button", "np-cell", e); b.type = "button";
    b.onclick = function () { set(i); grid.hidden = true; };
    grid.append(b);
    return b;
  });
  box.append(grid);

  function set(i, pop) {
    idx = (i + A.length) % A.length;
    var e = A[idx];
    face.textContent = e;
    face.style.background = window.cartasAvatarColor(e);
    hidden.value = e;
    window.cartasAvatarSet(e);
    cells.forEach(function (c, k) { c.classList.toggle("on", k === idx); });
    if (pop !== false) { face.classList.remove("pop"); void face.offsetWidth; face.classList.add("pop"); }
  }
  prev.onclick = function () { set(idx - 1); };
  next.onclick = function () { set(idx + 1); };
  shuffle.onclick = function () {
    var j = idx;
    while (A.length > 1 && j === idx) j = Math.floor(Math.random() * A.length);
    set(j);
  };
  face.onclick = function () { grid.hidden = !grid.hidden; };
  set(idx, false);

  form.insertBefore(box, form.firstChild);
  inp.placeholder = inp.placeholder || "Tu apodo";
  inp.setAttribute("autocomplete", "off");
  form.addEventListener("submit", function () { window.cartasAvatarSet(A[idx]); });
})();