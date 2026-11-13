// Theme toggle. The inline snippet in the page head applies a stored choice
// before the first paint; this only keeps the button and the choice in sync.
// With no choice stored, the page follows the operating system.

(function () {
  "use strict";

  const KEY = "theme";
  const root = document.documentElement;
  const button = document.getElementById("theme-toggle");
  if (!button) return;

  const darkQuery = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;

  function stored() {
    try {
      return localStorage.getItem(KEY);
    } catch (e) {
      return null; // Private windows and blocked site data.
    }
  }

  function current() {
    return stored() || (darkQuery && darkQuery.matches ? "dark" : "light");
  }

  // A half filled circle, flipped to show which way the switch goes. Both
  // glyphs have a text presentation everywhere, unlike a sun or a moon, which
  // some systems render as a colour emoji.
  function apply(theme) {
    const label = theme === "dark" ? "Switch to the light theme" : "Switch to the dark theme";
    root.setAttribute("data-theme", theme);
    button.textContent = theme === "dark" ? "\u25D1" : "\u25D0";
    button.setAttribute("aria-label", label);
    button.setAttribute("title", label);
  }

  apply(current());
  button.hidden = false;

  button.addEventListener("click", function () {
    const next = current() === "dark" ? "light" : "dark";
    try {
      localStorage.setItem(KEY, next);
    } catch (e) {
      /* The choice then lasts for this page only. */
    }
    apply(next);
  });

  // Follow the system for as long as the reader has not chosen.
  if (darkQuery && darkQuery.addEventListener) {
    darkQuery.addEventListener("change", function () {
      if (!stored()) apply(darkQuery.matches ? "dark" : "light");
    });
  }
})();
