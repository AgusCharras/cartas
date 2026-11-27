/* Personajes (emojis) que se pueden elegir. Misma lista que game/avatars.py (el servidor valida contra esa). */
window.CARTAS_AVATARS = ["🦆", "🐱", "🐶", "🦊", "🐸", "🐼", "🦉", "🐙", "🦄", "🐵", "🐢", "🦖", "🐧", "🐯", "🦁", "🐷", "🐰", "🐻", "🐨", "🐔", "🦜", "🐊", "🦙", "🐺", "🐝", "🦈", "🐬", "🦋", "🐞", "🦔", "🐭", "🐮", "👽", "👻", "🤖", "💀", "🤡", "😎", "🤠", "🧙", "🧛", "🧟", "🧉", "🥩", "🍕", "🍔", "🌭", "🥑", "🍩", "⚽"];

(function () {
  var KEY = "cartasAvatar";
  function hash(str) {
    var h = 5381;
    for (var ch of String(str)) h = ((h << 5) + h + ch.codePointAt(0)) | 0;
    return Math.abs(h);
  }
  window.cartasAvatarColor = function (key) { return "hsl(" + (hash(key) % 360) + " 55% 38%)"; };
  window.cartasAvatarGet = function () {
    try { var v = localStorage.getItem(KEY); return window.CARTAS_AVATARS.indexOf(v) >= 0 ? v : null; } catch (e) { return null; }
  };
  window.cartasAvatarSet = function (v) { try { localStorage.setItem(KEY, v); } catch (e) { /* sin almacenamiento: no se recuerda */ } };
})();