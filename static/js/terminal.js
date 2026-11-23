const terminal = document.querySelector("[data-terminal-form]");
if (terminal) {
  const output = terminal.querySelector("[data-terminal-output]");
  const status = terminal.querySelector("[data-terminal-status]");
  const input = terminal.elements.command;
  const run = terminal.querySelector('button[type="submit"]');
  let running = false;
  const append = (text) => {
    output.textContent = (output.textContent + text).slice(-160000);
    output.scrollTop = output.scrollHeight;
  };
  terminal.querySelector("[data-terminal-clear]").addEventListener("click", () => {
    output.textContent = "";
    input.focus();
  });
  terminal.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (running || !input.value.trim()) return;
    const data = new FormData(terminal);
    append(`$ ${input.value}\n`);
    input.value = "";
    running = true;
    run.disabled = true;
    status.textContent = "Running…";
    try {
      const response = await fetch(terminal.action, {
        method: "POST", body: data, credentials: "same-origin", redirect: "error",
      });
      if (!response.headers.get("Content-Type")?.includes("application/json")) {
        throw new Error("Command was rejected. Reload and sign in again if needed.");
      }
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "Could not run command.");
      if (result.output) append(result.output + (result.output.endsWith("\n") ? "" : "\n"));
      if (result.truncated) append("[Output truncated at 64 KiB]\n");
      status.textContent = `Exit code ${result.exit_code}`;
      append(`[Exit ${result.exit_code}]\n\n`);
    } catch (error) {
      status.textContent = error.message;
      append(`[${error.message}]\n\n`);
    } finally {
      running = false;
      run.disabled = false;
      input.focus();
    }
  });
}
