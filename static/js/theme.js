/* Apply the saved theme before first paint and wire the sidebar toggle. */
(function () {
  var root = document.documentElement;
  var meta = document.getElementById("meta-theme-color");

  function current() {
    return root.getAttribute("data-theme") || "light";
  }

  function updateLabel(theme) {
    var button = document.getElementById("theme-toggle");
    var label = button && button.querySelector(".theme-toggle-text");
    if (label) label.textContent = theme === "dark" ? "Light mode" : "Dark mode";
    if (meta) meta.setAttribute("content", theme === "dark" ? "#090a0d" : "#f5f6f8");
  }

  function apply(theme) {
    root.setAttribute("data-theme", theme);
    updateLabel(theme);
  }

  var saved = null;
  try {
    saved = localStorage.getItem("waggle_theme") || localStorage.getItem("taskk_theme");
  } catch (error) {
    saved = null;
  }
  var prefersDark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
  apply(saved || (prefersDark ? "dark" : "light"));

  document.addEventListener("DOMContentLoaded", function () {
    var button = document.getElementById("theme-toggle");
    if (!button) return;
    updateLabel(current());
    button.addEventListener("click", function () {
      var next = current() === "dark" ? "light" : "dark";
      apply(next);
      try {
        localStorage.setItem("waggle_theme", next);
      } catch (error) {
        /* Private mode or storage disabled: the choice lasts for this page. */
      }
    });
  });
})();
