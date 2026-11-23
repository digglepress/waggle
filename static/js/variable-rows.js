const add = document.querySelector("[data-variable-add]");
if (add) {
  const rows = document.querySelector("[data-variable-rows]");
  const template = document.querySelector("[data-variable-template]");
  const total = document.getElementById("id_variables-TOTAL_FORMS");
  add.addEventListener("click", () => {
    const index = Number(total.value);
    if (index >= 201) return;
    rows.insertAdjacentHTML("beforeend", template.innerHTML.replaceAll("__prefix__", String(index)));
    total.value = String(index + 1);
    rows.lastElementChild.querySelector("input").focus();
  });
}
