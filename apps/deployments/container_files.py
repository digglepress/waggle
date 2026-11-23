"""Bounded, read-only filesystem operations inside a selected service replica."""

import base64
import codecs
import shlex
from pathlib import PurePosixPath

PREVIEW_LIMIT = 24 * 1024
ENTRY_LIMIT = 300

LIST_DIRECTORY = r"""
[ -d "$1" ] && [ ! -L "$1" ] || exit 2
find "$1" -mindepth 1 -maxdepth 1 -exec sh -c '
  for item do
    if [ -L "$item" ]; then kind=link
    elif [ -d "$item" ]; then kind=directory
    elif [ -f "$item" ]; then kind=file
    else kind=other
    fi
    printf "%s\000%s\000" "$kind" "${item##*/}"
  done
' waggle-file-entry {} +
"""

PREVIEW_FILE = f"""
[ -f "$1" ] && [ ! -L "$1" ] && [ -r "$1" ] || exit 2
head -c {PREVIEW_LIMIT + 1} -- "$1" | base64
"""


class BrowserError(ValueError):
    pass


def browse_path(raw):
    if len(raw) > 1024 or not raw.startswith("/") or raw.startswith("//"):
        raise BrowserError("Enter an absolute container path, such as /app.")
    if any(ord(char) < 32 or ord(char) == 127 for char in raw):
        raise BrowserError("Control characters are not supported in browser paths.")
    path = PurePosixPath(raw)
    if ".." in path.parts:
        raise BrowserError("Use the parent-folder button instead of '..' in a path.")
    return path


def file_command(action, path):
    script = {"list": LIST_DIRECTORY, "preview": PREVIEW_FILE}.get(action)
    if script is None:
        raise BrowserError("Unknown filesystem operation.")
    return shlex.join(["/bin/sh", "-c", script, "waggle-files", str(path)])


def parse_file_response(action, path, result):
    if result["exit_code"]:
        if result["exit_code"] == 2:
            raise BrowserError("Path is missing, unreadable, or not a regular file or directory.")
        if result["exit_code"] == 125:
            raise BrowserError("This task no longer accepts container access. Refresh the page or redeploy it.")
        raise BrowserError("Could not read this path. The image needs sh, find, head, base64 and timeout.")
    if action == "preview":
        try:
            data = base64.b64decode("".join(result["output"].split()), validate=True)
        except ValueError:
            raise BrowserError("Could not decode the file preview.") from None
        if result["truncated"]:
            raise BrowserError("The file preview could not be read completely.")
        truncated = len(data) > PREVIEW_LIMIT
        try:
            decoder = codecs.getincrementaldecoder("utf-8")()
            text = decoder.decode(data[:PREVIEW_LIMIT], final=not truncated)
            binary = "\0" in text
        except UnicodeDecodeError:
            text, binary = "", True
        return {
            "path": str(path), "parent": str(path.parent), "text": "" if binary else text,
            "binary": binary, "truncated": truncated,
        }

    records = result["output"].split("\0")
    # If the transport capped its output, drop the final partial record.
    if not result["truncated"] and (records[-1] or len(records) % 2 != 1):
        raise BrowserError("Container did not return a complete directory listing.")
    records = records[:-1]
    entries = []
    for kind, name in zip(records[::2], records[1::2], strict=False):
        if kind not in {"directory", "file", "link", "other"} or not name or "/" in name:
            raise BrowserError("Container did not return a valid directory listing.")
        entries.append({"kind": kind, "name": name, "path": str(path / name)})
    entries.sort(key=lambda entry: (entry["kind"] != "directory", entry["name"].casefold()))
    return {
        "path": str(path), "parent": str(path.parent), "entries": entries[:ENTRY_LIMIT],
        "truncated": result["truncated"] or len(entries) > ENTRY_LIMIT,
    }
