import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

from tools import Tools, Workspace


class TestWorkspace(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_resolve_inside_workspace(self):
        workspace = Workspace(str(self.root))

        target = workspace.resolve("network.tf")

        self.assertEqual(
            target,
            (self.root / "network.tf").resolve(),
        )

    def test_resolve_outside_workspace_is_denied(self):
        workspace = Workspace(str(self.root))

        with self.assertRaises(ValueError):
            workspace.resolve("../secret.txt")


class TestTools(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)

        (self.root / "network.tf").write_text(
            """
resource "aws_vpc" "vigil" {
  cidr_block = "10.0.0.0/16"
}

resource "aws_route_table" "private" {
  vpc_id = aws_vpc.vigil.id
}
""".strip(),
            encoding="utf-8",
        )

        (self.root / "compute.tf").write_text(
            """
resource "aws_instance" "agent" {
  associate_public_ip_address = false
}
""".strip(),
            encoding="utf-8",
        )

        (self.root / "README.md").write_text(
            "VIGIL",
            encoding="utf-8",
        )

        self.workspace = Workspace(str(self.root))
        self.tools = Tools(self.workspace)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_list_files(self):
        result = self.tools.list_files("*.tf")

        self.assertIn("network.tf", result)
        self.assertIn("compute.tf", result)
        self.assertNotIn("README.md", result)

    def test_list_files_no_matches(self):
        result = self.tools.list_files("*.xyz")

        self.assertEqual(result, "0 files")

    def test_read_file(self):
        result = self.tools.read_file("compute.tf")

        self.assertIn(
            "associate_public_ip_address = false",
            result,
        )

    def test_read_missing_file(self):
        result = self.tools.read_file("missing.tf")

        self.assertEqual(
            result,
            "File not found: missing.tf",
        )

    def test_read_outside_workspace_is_denied(self):
        with self.assertRaises(ValueError):
            self.tools.read_file("../secret.txt")

    def test_grep(self):
        result = self.tools.grep(
            "aws_route_table",
            "*.tf",
        )

        self.assertIn(
            "network.tf:",
            result,
        )
        self.assertIn(
            'resource "aws_route_table"',
            result,
        )

    def test_grep_case_insensitive(self):
        result = self.tools.grep(
            "AWS_INSTANCE",
            "*.tf",
        )

        self.assertIn(
            "compute.tf:",
            result,
        )

    def test_grep_no_matches(self):
        result = self.tools.grep(
            "nat_gateway",
            "*.tf",
        )

        self.assertEqual(
            result,
            "0 matches",
        )

    @patch("socket.create_connection")
    def test_check_internet_connected(
        self,
        create_connection,
    ):
        connection = Mock()
        connection.__enter__ = Mock(
            return_value=connection
        )
        connection.__exit__ = Mock(
            return_value=False
        )

        create_connection.return_value = connection

        result = self.tools.check_internet(
            host="1.1.1.1",
            port=443,
            timeout=1,
        )

        self.assertEqual(
            result,
            "CONNECTED: 1.1.1.1:443",
        )

        create_connection.assert_called_once_with(
            ("1.1.1.1", 443),
            timeout=1,
        )

    @patch("socket.create_connection")
    def test_check_internet_unreachable(
        self,
        create_connection,
    ):
        create_connection.side_effect = TimeoutError(
            "timed out"
        )

        result = self.tools.check_internet(
            host="1.1.1.1",
            port=443,
            timeout=1,
        )

        self.assertIn(
            "UNREACHABLE: 1.1.1.1:443",
            result,
        )

        self.assertIn(
            "timed out",
            result,
        )

    @patch("subprocess.run")
    def test_check_s3_success(
        self,
        subprocess_run,
    ):
        subprocess_run.return_value = Mock(
            returncode=0,
            stdout=(
                "2026-08-11 12:00:00 "
                "123 vigil-bundle.tar.gz\n"
            ),
            stderr="",
        )

        result = self.tools.check_s3(
            "vigil-artifacts"
        )

        self.assertIn(
            "exit_code=0",
            result,
        )

        self.assertIn(
            "vigil-bundle.tar.gz",
            result,
        )

        subprocess_run.assert_called_once()

    @patch("subprocess.run")
    def test_check_s3_access_denied(
        self,
        subprocess_run,
    ):
        subprocess_run.return_value = Mock(
            returncode=1,
            stdout="",
            stderr="AccessDenied",
        )

        result = self.tools.check_s3(
            "vigil-artifacts"
        )

        self.assertIn(
            "exit_code=1",
            result,
        )

        self.assertIn(
            "AccessDenied",
            result,
        )

    def test_registry(self):
        registry = self.tools.registry()

        self.assertEqual(
            set(registry.keys()),
            {
                "list_files",
                "read_file",
                "grep",
                "check_internet",
                "check_s3",
            },
        )

        for tool in registry.values():
            self.assertTrue(callable(tool))

    def test_grep_regex_or(self):
        result = self.tools.grep(
            "aws_vpc|internet_gateway",
            "*.tf",
        )

        self.assertIn("network.tf:", result)
        self.assertIn(
            'resource "aws_vpc"',
            result,
        )

    def test_grep_invalid_regex(self):
        result = self.tools.grep(
            "[invalid",
            "*.tf",
        )

        self.assertIn(
            "ERROR: invalid regex",
            result,
        )


if __name__ == "__main__":
    unittest.main()