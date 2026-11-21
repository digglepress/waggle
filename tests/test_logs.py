from __future__ import annotations

import tempfile
from pathlib import Path

from django.test import SimpleTestCase

from apps.core.logs import append_line, read_new_lines, reset_log


class LogTailTests(SimpleTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "server-1.log"

    def test_missing_file_returns_nothing(self):
        lines, position = read_new_lines(self.path, 0)
        self.assertEqual(lines, [])
        self.assertEqual(position, 0)

    def test_incremental_reads(self):
        append_line(self.path, "one")
        append_line(self.path, "two")
        lines, position = read_new_lines(self.path, 0)
        self.assertEqual(lines, ["one", "two"])

        append_line(self.path, "three")
        lines, position = read_new_lines(self.path, position)
        self.assertEqual(lines, ["three"])

        lines, _ = read_new_lines(self.path, position)
        self.assertEqual(lines, [])

    def test_partial_line_is_held_until_complete(self):
        self.path.write_text("complete\nhalf", encoding="utf-8")
        lines, position = read_new_lines(self.path, 0)
        self.assertEqual(lines, ["complete"])

        with self.path.open("a", encoding="utf-8") as handle:
            handle.write("-done\n")
        lines, _ = read_new_lines(self.path, position)
        self.assertEqual(lines, ["half-done"])

    def test_reset_truncates(self):
        append_line(self.path, "old")
        reset_log(self.path)
        self.assertEqual(self.path.read_text(encoding="utf-8"), "")
        lines, _ = read_new_lines(self.path, 0)
        self.assertEqual(lines, [])

    def test_truncated_file_resets_position(self):
        append_line(self.path, "a long line")
        _, position = read_new_lines(self.path, 0)
        reset_log(self.path)
        append_line(self.path, "new")
        lines, _ = read_new_lines(self.path, position)
        self.assertEqual(lines, ["new"])
