/* Switch the project index between card and list layouts, and live filter by keyword. */
(function () {
  'use strict';

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

  // Client-side search input filter
  var searchInput = document.getElementById("service-search-filter");
  if (searchInput) {
    searchInput.addEventListener("input", function () {
      var query = searchInput.value.trim().toLowerCase();
      var projectCards = document.querySelectorAll(".project-card");
      var projectRows = document.querySelectorAll(".project-list-row, .service-item-row");

      function match(el) {
        if (!query) return true;
        var text = (el.textContent || "").toLowerCase();
        return text.indexOf(query) !== -1;
      }

      projectCards.forEach(function (card) {
        card.style.display = match(card) ? "" : "none";
      });

      projectRows.forEach(function (row) {
        row.style.display = match(row) ? "" : "none";
      });
    });
  }
})();
