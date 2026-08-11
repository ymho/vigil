import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from tools import Tools, Workspace


class TestWorkspace(unittest.TestCase):

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_workspace_exists(self):
        workspace = Workspace(str(self.root))

        self.assertEqual(
            workspace.root,
            self.root.resolve(),
        )

    def test_resolve_inside_workspace(self):
        workspace = Workspace(str(self.root))

        result = workspace.resolve("network.tf")

        self.assertEqual(
            result,
            (self.root / "network.tf").resolve(),
        )

    def test_resolve_outside_workspace_denied(self):
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
  cidr_block = "10.40.0.0/16"
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

        subdir = self.root / "docs"
        subdir.mkdir()

        (subdir / "README.md").write_text(
            "VIGIL documentation",
            encoding="utf-8",
        )

        self.workspace = Workspace(
            str(self.root)
        )

        self.tools = Tools(
            self.workspace
        )

    def tearDown(self):
        self.tempdir.cleanup()

    # --------------------------------------------------------
    # Files
    # --------------------------------------------------------

    def test_list_files(self):
        result = self.tools.list_files("*.tf")

        self.assertIn(
            "network.tf",
            result,
        )

        self.assertIn(
            "compute.tf",
            result,
        )

    def test_list_files_no_match(self):
        result = self.tools.list_files("*.xyz")

        self.assertEqual(
            result,
            "0 files",
        )

    def test_list_directory(self):
        result = self.tools.list_directory(".")

        self.assertIn(
            "docs/",
            result,
        )

        self.assertIn(
            "network.tf",
            result,
        )

    def test_read_file(self):
        result = self.tools.read_file(
            "network.tf"
        )

        self.assertIn(
            'resource "aws_vpc"',
            result,
        )

    def test_read_file_lines(self):
        result = self.tools.read_file(
            "network.tf",
            start_line=1,
            end_line=2,
        )

        self.assertIn(
            "1:",
            result,
        )

    def test_read_outside_workspace_denied(self):
        with self.assertRaises(ValueError):
            self.tools.read_file(
                "../secret"
            )

    def test_search_text_regex(self):
        result = self.tools.search_text(
            "aws_vpc|aws_route_table",
            "*.tf",
        )

        self.assertIn(
            "network.tf:",
            result,
        )

    def test_search_text_plain(self):
        result = self.tools.search_text(
            "PUBLIC_IP",
            "*.tf",
            regex=False,
        )

        self.assertIn(
            "compute.tf:",
            result,
        )

    def test_search_invalid_regex(self):
        result = self.tools.search_text(
            "[invalid",
            "*.tf",
        )

        self.assertIn(
            "ERROR: invalid regex",
            result,
        )

    def test_file_stat(self):
        result = self.tools.file_stat(
            "network.tf"
        )

        self.assertIn(
            '"type": "file"',
            result,
        )

    def test_hash_file(self):
        result = self.tools.hash_file(
            "network.tf",
            "sha256",
        )

        self.assertTrue(
            result.startswith("sha256=")
        )

    # --------------------------------------------------------
    # DNS
    # --------------------------------------------------------

    @patch("socket.getaddrinfo")
    def test_resolve_dns(
        self,
        getaddrinfo,
    ):
        getaddrinfo.return_value = [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                6,
                "",
                ("10.0.0.1", 0),
            )
        ]

        result = self.tools.resolve_dns(
            "example.local"
        )

        self.assertEqual(
            result,
            "10.0.0.1",
        )

    # --------------------------------------------------------
    # TCP
    # --------------------------------------------------------

    @patch("socket.create_connection")
    def test_check_tcp_connected(
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

        create_connection.return_value = (
            connection
        )

        result = self.tools.check_tcp(
            "1.1.1.1",
            443,
            1,
        )

        self.assertIn(
            "status=CONNECTED",
            result,
        )

    @patch("socket.create_connection")
    def test_check_tcp_timeout(
        self,
        create_connection,
    ):
        create_connection.side_effect = (
            socket.timeout()
        )

        result = self.tools.check_tcp(
            "1.1.1.1",
            443,
            1,
        )

        self.assertIn(
            "status=TIMEOUT",
            result,
        )

    @patch("socket.create_connection")
    def test_check_tcp_refused(
        self,
        create_connection,
    ):
        create_connection.side_effect = (
            ConnectionRefusedError()
        )

        result = self.tools.check_tcp(
            "127.0.0.1",
            1234,
            1,
        )

        self.assertIn(
            "status=REFUSED",
            result,
        )

    # --------------------------------------------------------
    # HTTP
    # --------------------------------------------------------

    def test_http_invalid_scheme(self):
        result = self.tools.http_request(
            "ftp://example.com"
        )

        self.assertIn(
            "only http and https",
            result,
        )

    # --------------------------------------------------------
    # Programs
    # --------------------------------------------------------

    @patch("subprocess.run")
    def test_get_routes(
        self,
        run,
    ):
        run.return_value = Mock(
            returncode=0,
            stdout=(
                "10.40.0.0/16 dev eth0\n"
            ),
            stderr="",
        )

        result = self.tools.get_routes()

        self.assertIn(
            "10.40.0.0/16",
            result,
        )

    @patch("subprocess.run")
    def test_get_interfaces(
        self,
        run,
    ):
        run.return_value = Mock(
            returncode=0,
            stdout=(
                "eth0 UP 10.40.1.10/24\n"
            ),
            stderr="",
        )

        result = (
            self.tools.get_interfaces()
        )

        self.assertIn(
            "eth0",
            result,
        )

    @patch("subprocess.run")
    def test_get_listening_ports(
        self,
        run,
    ):
        run.return_value = Mock(
            returncode=0,
            stdout=(
                "LISTEN 127.0.0.1:11434"
            ),
            stderr="",
        )

        result = (
            self.tools.get_listening_ports()
        )

        self.assertIn(
            "11434",
            result,
        )

    @patch("subprocess.run")
    def test_run_program_allowed(
        self,
        run,
    ):
        run.return_value = Mock(
            returncode=0,
            stdout="Linux",
            stderr="",
        )

        result = self.tools.run_program(
            "uname",
            ["-s"],
        )

        self.assertIn(
            "Linux",
            result,
        )

    def test_run_program_denied(self):
        result = self.tools.run_program(
            "bash",
            ["-c", "whoami"],
        )

        self.assertIn(
            "program not allowed",
            result,
        )

    # --------------------------------------------------------
    # Registry
    # --------------------------------------------------------

    def test_registry(self):
        registry = self.tools.registry()

        expected = {
            "list_files",
            "list_directory",
            "read_file",
            "search_text",
            "file_stat",
            "hash_file",
            "resolve_dns",
            "check_tcp",
            "http_request",
            "get_routes",
            "get_interfaces",
            "get_listening_ports",
            "run_program",
        }

        self.assertEqual(
            set(registry.keys()),
            expected,
        )

        for function in registry.values():
            self.assertTrue(
                callable(function)
            )


if __name__ == "__main__":
    unittest.main()