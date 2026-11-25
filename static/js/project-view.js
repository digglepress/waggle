/* Switch the project index between card and list layouts. */
(function () {
  function show(view) {
    var cards = document.getElementById("view-cards");
    var list = document.getElementById("view-list");
    if (cards) cards.style.display = view === "list" ? "none" : "block";
    if (list) list.style.display = view === "list" ? "block" : "none";
  }

  document.querySelectorAll(".view-tab").forEach(function (tab) {
    tab.addEventListener("click", function () {
      document.querySelectorAll(".view-tab").forEach(function (other) {
        other.classList.remove("active");
        other.setAttribute("aria-selected", "false");
      });
      tab.classList.add("active");
      tab.setAttribute("aria-selected", "true");
      show(tab.getAttribute("data-view"));
    });
  });
})();
