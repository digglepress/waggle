/* Tail the bootstrap log over server-sent events.
 *
 * The server writes lines to a file; the SSE endpoint tails it. When the
 * server leaves a transient state the endpoint sends `event: done` and we
 * close the connection. We deliberately do not auto-reload: reloading on
 * every `done` would loop for an idle page.
 */
(function () {
  const el = document.getElementById("bootstrap-log");
  if (!el || !el.dataset.streamUrl) return;

  const source = new EventSource(el.dataset.streamUrl);

  source.onmessage = function (event) {
    let line;
    try {
      line = JSON.parse(event.data);
    } catch (_err) {
      line = event.data;
    }
    el.textContent += line + "\n";
    el.scrollTop = el.scrollHeight;
  };

  source.addEventListener("done", function () {
    source.close();
    if (!el.textContent.trim()) {
      el.textContent = "No output yet. The task may still be queued.\n";
    }
    el.dataset.closed = "1";
  });

  source.onerror = function () {
    // EventSource retries on its own; only stop if the server said done.
    if (el.dataset.closed === "1") source.close();
  };
})();
