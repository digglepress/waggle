const filesForm = document.querySelector("[data-files-form]");
if (filesForm) {
  const status = filesForm.querySelector("[data-files-status]");
  const listing = filesForm.querySelector("[data-files-list]");
  const preview = filesForm.querySelector("[data-files-preview]");
  const up = filesForm.querySelector("[data-files-up]");
  const browse = filesForm.querySelector('button[type="submit"]');
  const pathInput = filesForm.elements.path;
  const task = filesForm.elements.task;
  let current = "/";
  let parent = "/";
  let busy = false;

  async function open(action, path) {
    if (busy) return;
    busy = true;
    for (const control of [up, browse, task]) control.disabled = true;
    status.textContent = "Reading container…";
    const body = new FormData(filesForm);
    body.set("task", task.value);
    body.set("path", path);
    body.set("action", action);
    try {
      const response = await fetch(filesForm.action, {
        method: "POST", body, credentials: "same-origin", redirect: "error",
      });
      if (!response.headers.get("Content-Type")?.includes("application/json")) {
        throw new Error("The request was rejected. Reload and sign in again if needed.");
      }
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "The path could not be opened.");
      if (action === "preview") {
        preview.hidden = false;
        preview.textContent = result.binary ? "Binary file. Text preview unavailable." : result.text || "(empty file)";
        status.textContent = result.path + (result.truncated ? " · first 24 KiB" : "");
        return;
      }
      current = result.path;
      parent = result.parent;
      pathInput.value = current;
      preview.textContent = "";
      preview.hidden = true;
      listing.replaceChildren();
      for (const entry of result.entries) {
        const row = document.createElement("li");
        if (entry.kind === "directory" || entry.kind === "file") {
          const button = document.createElement("button");
          button.type = "button";
          button.className = "link mono";
          button.textContent = entry.name + (entry.kind === "directory" ? "/" : "");
          button.addEventListener("click", () => open(entry.kind === "directory" ? "list" : "preview", entry.path));
          row.append(button);
        } else {
          row.textContent = `${entry.name} (${entry.kind})`;
          row.className = "muted mono";
        }
        listing.append(row);
      }
      status.textContent = `${current} · ${result.entries.length} entries${result.truncated ? " (truncated)" : ""}`;
    } catch (error) {
      status.textContent = error.message;
    } finally {
      busy = false;
      for (const control of [up, browse, task]) control.disabled = false;
      up.disabled = current === "/";
    }
  }

  filesForm.addEventListener("submit", (event) => {
    event.preventDefault();
    open("list", pathInput.value);
  });
  up.addEventListener("click", () => open("list", parent));
  task.addEventListener("change", () => {
    listing.replaceChildren();
    preview.textContent = "";
    preview.hidden = true;
    current = "/";
    parent = "/";
    pathInput.value = "/";
    open("list", "/");
  });
  open("list", "/");
}
