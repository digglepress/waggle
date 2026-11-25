const log = document.querySelector("[data-log-stream]");
if (log && window.EventSource) {
  const source = new EventSource(log.dataset.logStream);
  const status = document.querySelector("[data-log-status]");
  const lines = [];
  const seen = new Set();
  source.onmessage = (event) => {
    const line = JSON.parse(event.data);
    if (log.dataset.deduplicate && seen.has(line)) return;
    if (log.dataset.deduplicate) seen.add(line);
    const follow = log.scrollHeight - log.scrollTop - log.clientHeight < 30;
    lines.push(line);
    if (lines.length > 1000) seen.delete(lines.shift());
    log.textContent = lines.join("\n");
    if (follow) log.scrollTop = log.scrollHeight;
    if (status) status.textContent = "Streaming";
  };
  source.addEventListener("done", (event) => {
    source.close();
    const label = JSON.parse(event.data);
    if (status) status.textContent = label;
    const jobState = document.querySelector("[data-job-state]");
    if (jobState) jobState.textContent = label;
  });
  source.onerror = () => {
    if (status) status.textContent = "Reconnecting…";
  };
}
