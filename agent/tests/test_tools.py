import os
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tools


class ToolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["VIGIL_WORKSPACE"] = self.tmp.name
        root = Path(self.tmp.name)
        (root / "network.tf").write_text('resource "aws_route_table" "private" {}\n', encoding="utf-8")
        (root / "notes.txt").write_text("hello\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_list_files(self):
        self.assertIn("network.tf", tools.list_files("*.tf"))

    def test_read_file(self):
        self.assertIn("aws_route_table", tools.read_file("network.tf"))

    def test_path_escape_blocked(self):
        with self.assertRaises(ValueError):
            tools.read_file("../outside")

    def test_grep(self):
        self.assertIn("network.tf:1", tools.grep("route_table"))
        self.assertEqual("0 matches", tools.grep("nat_gateway"))


if __name__ == "__main__":
    unittest.main()
