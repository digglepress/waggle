import os
import signal
import subprocess
import threading
from datetime import UTC, datetime

from apps.core.logs import append_line

BUILD_LOG_LIMIT = 8 * 1024 * 1024


def at_time(message):
    return f"{datetime.now(UTC).isoformat(timespec='milliseconds')} {message}"


def run_build(command, *, env, log_path, timeout):
    """Drain build output while the main worker enforces the build deadline."""
    unix = os.name == "posix"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env,
        stdin=subprocess.DEVNULL, start_new_session=unix,
    )
    errors = []

    def drain():
        size = 0
        try:
            with proc.stdout, log_path.open("ab") as log:
                while chunk := proc.stdout.readline(16_384):
                    if size >= BUILD_LOG_LIMIT:
                        continue
                    line = at_time(chunk.decode("utf-8", "replace").rstrip("\r\n"))
                    encoded = (line + "\n").encode("utf-8")
                    log.write(encoded)
                    log.flush()
                    size += len(encoded)
                    if size >= BUILD_LOG_LIMIT:
                        log.write((at_time("Build output limit reached.") + "\n").encode("utf-8"))
                        log.flush()
        except OSError as exc:
            errors.append(exc)

    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    try:
        code = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        if unix:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        else:
            proc.kill()
        proc.wait()
        reader.join(timeout=3)
        raise
    reader.join(timeout=3)
    if reader.is_alive() or errors:
        raise OSError("Could not finish the build log.")
    if code:
        raise subprocess.CalledProcessError(code, command)


def deployment_line(path, message):
    append_line(path, at_time(message.replace("\r", " ").replace("\n", " ")))
