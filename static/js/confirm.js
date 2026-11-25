/* Ask before submitting a dangerous form marked with data-confirm. */
document.addEventListener(
  "submit",
  function (event) {
    var form = event.target;
    if (!form || !form.getAttribute) return;
    var message = form.getAttribute("data-confirm");
    if (message && !window.confirm(message)) {
      event.preventDefault();
    }
  },
  true
);
