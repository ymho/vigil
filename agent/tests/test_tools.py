import socket
import tempfile
import unittest
import subprocess
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
        workspace = Workspace(
            str(self.root)
        )

        self.assertEqual(
            workspace.root,
            self.root.resolve(),
        )

    def test_workspace_must_be_directory(self):
        file = self.root / "x.txt"
        file.write_text(
            "x",
            encoding="utf-8",
        )

        with self.assertRaises(ValueError):
            Workspace(str(file))

    def test_resolve_inside_workspace(self):
        workspace = Workspace(
            str(self.root)
        )

        target = workspace.resolve(
            "network.tf"
        )

        self.assertEqual(
            target,
            (
                self.root
                / "network.tf"
            ).resolve(),
        )

    def test_resolve_outside_workspace_denied(self):
        workspace = Workspace(
            str(self.root)
        )

        with self.assertRaises(ValueError):
            workspace.resolve(
                "../secret.txt"
            )


class TestTools(unittest.TestCase):

    def setUp(self):
        self.tempdir = (
            tempfile.TemporaryDirectory()
        )

        self.root = Path(
            self.tempdir.name
        )

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

        (self.root / "endpoints.tf").write_text(
            """
resource "aws_vpc_endpoint" "s3" {
  vpc_endpoint_type = "Gateway"
}
""".strip(),
            encoding="utf-8",
        )

        docs = self.root / "docs"
        docs.mkdir()

        (docs / "README.md").write_text(
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

    # ========================================================
    # Files
    # ========================================================

    def test_list_files(self):
        result = self.tools.list_files(
            "*.tf"
        )

        self.assertIn(
            "network.tf",
            result,
        )

        self.assertIn(
            "compute.tf",
            result,
        )

        self.assertIn(
            "endpoints.tf",
            result,
        )

    def test_list_files_no_match(self):
        result = self.tools.list_files(
            "*.xyz"
        )

        self.assertEqual(
            result,
            "0 files",
        )

    def test_list_directory(self):
        result = (
            self.tools.list_directory(".")
        )

        self.assertIn(
            "docs/",
            result,
        )

        self.assertIn(
            "network.tf",
            result,
        )

    def test_list_directory_missing(self):
        result = (
            self.tools.list_directory(
                "missing"
            )
        )

        self.assertIn(
            "Path not found",
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

        self.assertIn(
            "1:",
            result,
        )

    def test_read_file_line_range(self):
        result = self.tools.read_file(
            "network.tf",
            start_line=1,
            end_line=2,
        )

        self.assertIn(
            "1:",
            result,
        )

        self.assertIn(
            "2:",
            result,
        )

    def test_read_file_missing(self):
        result = self.tools.read_file(
            "missing.txt"
        )

        self.assertEqual(
            result,
            "File not found: missing.txt",
        )

    def test_read_outside_workspace_denied(self):
        with self.assertRaises(ValueError):
            self.tools.read_file(
                "../secret"
            )

    def test_search_text_regex_or(self):
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

    def test_search_text_no_match(self):
        result = self.tools.search_text(
            "internet_gateway",
            "*.tf",
            regex=False,
        )

        self.assertEqual(
            result,
            "0 matches",
        )

    def test_search_text_invalid_regex(self):
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

        self.assertIn(
            '"size_bytes"',
            result,
        )

    def test_hash_file_sha256(self):
        result = self.tools.hash_file(
            "network.tf",
            "sha256",
        )

        self.assertTrue(
            result.startswith(
                "sha256="
            )
        )

    def test_hash_file_invalid_algorithm(self):
        result = self.tools.hash_file(
            "network.tf",
            "foo",
        )

        self.assertIn(
            "unsupported algorithm",
            result,
        )

    # ========================================================
    # DNS
    # ========================================================

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

        self.assertIn(
            "status=RESOLVED",
            result,
        )

        self.assertIn(
            "10.0.0.1",
            result,
        )

        self.assertIn(
            "does not establish",
            result,
        )

    @patch("socket.getaddrinfo")
    def test_resolve_dns_failure(
        self,
        getaddrinfo,
    ):
        getaddrinfo.side_effect = (
            socket.gaierror(
                "not found"
            )
        )

        result = self.tools.resolve_dns(
            "invalid"
        )

        self.assertIn(
            "status=ERROR",
            result,
        )

    # ========================================================
    # TCP
    # ========================================================

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

    # ========================================================
    # HTTP
    # ========================================================

    def test_http_invalid_scheme(self):
        result = self.tools.http_request(
            "ftp://example.com"
        )

        self.assertIn(
            "only http and https",
            result,
        )

    def test_http_invalid_method(self):
        result = self.tools.http_request(
            "https://example.com",
            method="POST",
        )

        self.assertIn(
            "only HEAD and GET",
            result,
        )

    # ========================================================
    # Linux OS observations
    # ========================================================

    @patch("subprocess.run")
    def test_get_os_routes(
        self,
        run,
    ):
        run.return_value = Mock(
            returncode=0,
            stdout=(
                "default via 10.40.1.1 "
                "dev enp39s0\n"
            ),
            stderr="",
        )

        result = (
            self.tools.get_os_routes()
        )

        self.assertIn(
            "scope=LINUX_OS_ROUTING_TABLE",
            result,
        )

        self.assertIn(
            "default via 10.40.1.1",
            result,
        )

        self.assertIn(
            "does not directly describe",
            result,
        )

    @patch("subprocess.run")
    def test_get_os_rules(
        self,
        run,
    ):
        run.return_value = Mock(
            returncode=0,
            stdout=(
                "0: from all lookup local\n"
            ),
            stderr="",
        )

        result = self.tools.get_os_rules()

        self.assertIn(
            "LINUX_OS_POLICY_ROUTING",
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
                "enp39s0 UP "
                "10.40.1.106/24\n"
            ),
            stderr="",
        )

        result = (
            self.tools.get_interfaces()
        )

        self.assertIn(
            "enp39s0",
            result,
        )

        self.assertIn(
            "LINUX_OS_INTERFACES",
            result,
        )

    @patch("subprocess.run")
    def test_get_neighbor_table(
        self,
        run,
    ):
        run.return_value = Mock(
            returncode=0,
            stdout=(
                "10.40.1.1 dev enp39s0 "
                "lladdr 00:00:00:00:00:01 "
                "REACHABLE"
            ),
            stderr="",
        )

        result = (
            self.tools.get_neighbor_table()
        )

        self.assertIn(
            "LINUX_OS_NEIGHBOR_TABLE",
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
                "LISTEN 0 4096 "
                "127.0.0.1:11434"
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

    # ========================================================
    # System
    # ========================================================

    @patch("socket.gethostname")
    def test_get_hostname(
        self,
        gethostname,
    ):
        gethostname.return_value = (
            "vigil-test"
        )

        result = self.tools.get_hostname()

        self.assertIn(
            "vigil-test",
            result,
        )

    def test_get_system_info(self):
        result = (
            self.tools.get_system_info()
        )

        self.assertIn(
            '"scope": "LOCAL_SYSTEM"',
            result,
        )

        self.assertIn(
            '"python"',
            result,
        )

    # ========================================================
    # Terraform
    # ========================================================

    @patch("subprocess.run")
    def test_terraform_validate(
        self,
        run,
    ):
        run.return_value = Mock(
            returncode=0,
            stdout=(
                "Success! The configuration "
                "is valid."
            ),
            stderr="",
        )

        result = (
            self.tools.terraform_validate()
        )

        self.assertIn(
            "exit_code=0",
            result,
        )

        self.assertIn(
            "configuration is valid",
            result,
        )

    @patch("subprocess.run")
    def test_terraform_state_list(
        self,
        run,
    ):
        run.return_value = Mock(
            returncode=0,
            stdout=(
                "aws_vpc.vigil\n"
                "aws_instance.agent\n"
            ),
            stderr="",
        )

        result = (
            self.tools.terraform_state_list()
        )

        self.assertIn(
            "aws_vpc.vigil",
            result,
        )

    @patch("subprocess.run")
    def test_terraform_state_show(
        self,
        run,
    ):
        run.return_value = Mock(
            returncode=0,
            stdout=(
                "# aws_vpc.vigil:\n"
                "resource \"aws_vpc\" \"vigil\" {}"
            ),
            stderr="",
        )

        result = (
            self.tools.terraform_state_show(
                "aws_vpc.vigil"
            )
        )

        self.assertIn(
            "aws_vpc",
            result,
        )

    def test_terraform_state_show_rejects_bad_address(
        self,
    ):
        result = (
            self.tools.terraform_state_show(
                "aws_vpc.vigil; rm -rf /"
            )
        )

        self.assertIn(
            "invalid Terraform resource address",
            result,
        )

    # ========================================================
    # Internal runner
    # ========================================================

    @patch("subprocess.run")
    def test_run_readonly_timeout(
        self,
        run,
    ):
        run.side_effect = (
            subprocess.TimeoutExpired(
                cmd=["ip"],
                timeout=5,
            )
        )

        result = self.tools._run_readonly(
            ["ip", "route"],
            timeout=5,
        )

        self.assertIn(
            "status=TIMEOUT",
            result,
        )

    @patch("subprocess.run")
    def test_run_readonly_not_found(
        self,
        run,
    ):
        run.side_effect = (
            FileNotFoundError()
        )

        result = self.tools._run_readonly(
            ["missing-program"]
        )

        self.assertIn(
            "status=NOT_FOUND",
            result,
        )

    # ========================================================
    # Registry
    # ========================================================

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
            "get_os_routes",
            "get_os_rules",
            "get_interfaces",
            "get_neighbor_table",
            "get_listening_ports",
            "get_hostname",
            "get_system_info",
            "terraform_validate",
            "terraform_state_list",
            "terraform_state_show",
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