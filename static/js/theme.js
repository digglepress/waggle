/* Apply the saved theme before first paint and wire all theme toggles. */
(function () {
  var root = document.documentElement;
  var meta = document.getElementById("meta-theme-color");

  function current() {
    return root.getAttribute("data-theme") || "light";
  }

  function updateLabel(theme) {
    var buttons = document.querySelectorAll(".theme-toggle-btn");
    buttons.forEach(function (button) {
      var label = button.querySelector(".theme-toggle-text");
      if (label) label.textContent = theme === "dark" ? "Light mode" : "Dark mode";
      button.setAttribute("title", theme === "dark" ? "Switch to light mode" : "Switch to dark mode");
      button.setAttribute("aria-label", theme === "dark" ? "Switch to light mode" : "Switch to dark mode");
    });
    if (meta) meta.setAttribute("content", theme === "dark" ? "#0b0d11" : "#f5f6f8");
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
    updateLabel(current());
    var buttons = document.querySelectorAll(".theme-toggle-btn");
    buttons.forEach(function (button) {
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
  });
})();
