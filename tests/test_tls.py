"""Tests for TLS configuration, SSL context builders, and certificate generation."""

from __future__ import annotations

import asyncio
import ssl
import tempfile
from pathlib import Path

import pytest

from hs_py.tls import (
    TLSConfig,
    build_client_ssl_context,
    build_server_ssl_context,
    extract_peer_cn,
    extract_peer_sans,
    generate_test_certificates,
)


class TestTLSConfig:
    def test_defaults(self) -> None:
        config = TLSConfig()
        assert config.certificate_path is None
        assert config.private_key_path is None
        assert config.ca_certificates_path is None
        assert config.key_password is None

    def test_custom_values(self) -> None:
        config = TLSConfig(
            certificate_path="/tmp/cert.pem",
            private_key_path="/tmp/key.pem",
            ca_certificates_path="/tmp/ca.pem",
            key_password=b"secret",
        )
        assert config.certificate_path == "/tmp/cert.pem"
        assert config.private_key_path == "/tmp/key.pem"
        assert config.ca_certificates_path == "/tmp/ca.pem"
        assert config.key_password == b"secret"

    def test_repr_redacts_sensitive_fields(self) -> None:
        config = TLSConfig(
            certificate_path="/tmp/cert.pem",
            private_key_path="/tmp/key.pem",
            key_password="secret",
        )
        r = repr(config)
        assert "/tmp/cert.pem" in r
        assert "/tmp/key.pem" not in r
        assert "secret" not in r
        assert "<REDACTED>" in r

    def test_repr_no_redaction_when_none(self) -> None:
        config = TLSConfig(certificate_path="/tmp/cert.pem")
        r = repr(config)
        assert "<REDACTED>" not in r

    def test_frozen(self) -> None:
        config = TLSConfig()
        with pytest.raises(AttributeError):
            config.certificate_path = "/tmp/cert.pem"  # type: ignore[misc]


class TestGenerateTestCertificates:
    def test_generates_all_files(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            config = generate_test_certificates(d)
            p = Path(d)
            assert (p / "ca.pem").exists()
            assert (p / "server.pem").exists()
            assert (p / "server.key").exists()
            assert (p / "client.pem").exists()
            assert (p / "client.key").exists()
            assert config.certificate_path == str(p / "server.pem")
            assert config.private_key_path == str(p / "server.key")
            assert config.ca_certificates_path == str(p / "ca.pem")

    def test_creates_directory_if_missing(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            sub = Path(d) / "nested" / "certs"
            config = generate_test_certificates(sub)
            assert sub.exists()
            assert config.certificate_path is not None

    def test_cert_files_are_pem(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            generate_test_certificates(d)
            p = Path(d)
            for name in ("ca.pem", "server.pem", "client.pem"):
                content = (p / name).read_text()
                assert content.startswith("-----BEGIN CERTIFICATE-----")
            for name in ("server.key", "client.key"):
                content = (p / name).read_text()
                assert content.startswith("-----BEGIN PRIVATE KEY-----")


class TestBuildClientSSLContext:
    def test_tls_13_minimum(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            config = generate_test_certificates(d)
            # Use client cert + key for the client context
            client_config = TLSConfig(
                certificate_path=str(Path(d) / "client.pem"),
                private_key_path=str(Path(d) / "client.key"),
                ca_certificates_path=config.ca_certificates_path,
            )
            ctx = build_client_ssl_context(client_config)
            assert ctx.minimum_version == ssl.TLSVersion.TLSv1_3
            assert ctx.verify_mode == ssl.CERT_REQUIRED  # PROTOCOL_TLS_CLIENT default
            assert ctx.check_hostname is True

    def test_no_cert_chain(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            config = generate_test_certificates(d)
            # Only CA, no client cert
            client_config = TLSConfig(ca_certificates_path=config.ca_certificates_path)
            ctx = build_client_ssl_context(client_config)
            assert ctx.minimum_version == ssl.TLSVersion.TLSv1_3

    def test_no_ca_certs(self) -> None:
        config = TLSConfig()
        ctx = build_client_ssl_context(config)
        assert ctx.minimum_version == ssl.TLSVersion.TLSv1_3


class TestBuildServerSSLContext:
    def test_tls_13_minimum(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            config = generate_test_certificates(d)
            ctx = build_server_ssl_context(config)
            assert ctx.minimum_version == ssl.TLSVersion.TLSv1_3
            assert ctx.verify_mode == ssl.CERT_REQUIRED

    def test_mutual_auth_required(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            config = generate_test_certificates(d)
            ctx = build_server_ssl_context(config)
            assert ctx.verify_mode == ssl.CERT_REQUIRED


class TestTLSHandshake:
    async def test_client_server_tls_13_handshake(self) -> None:
        """Verify a TLS 1.3 handshake succeeds between generated certs."""
        with tempfile.TemporaryDirectory() as d:
            server_config = generate_test_certificates(d)
            client_config = TLSConfig(
                certificate_path=str(Path(d) / "client.pem"),
                private_key_path=str(Path(d) / "client.key"),
                ca_certificates_path=str(Path(d) / "ca.pem"),
            )

            server_ctx = build_server_ssl_context(server_config)
            client_ctx = build_client_ssl_context(client_config)

            # Start a TLS server
            connected = asyncio.Event()
            handshake_ok = False

            async def handle_client(
                reader: asyncio.StreamReader, writer: asyncio.StreamWriter
            ) -> None:
                nonlocal handshake_ok
                data = await reader.read(5)
                assert data == b"hello"
                writer.write(b"world")
                await writer.drain()
                handshake_ok = True
                connected.set()
                writer.close()
                await writer.wait_closed()

            server = await asyncio.start_server(handle_client, "127.0.0.1", 0, ssl=server_ctx)
            addr = server.sockets[0].getsockname()

            # Connect as TLS client
            reader, writer = await asyncio.open_connection(
                addr[0], addr[1], ssl=client_ctx, server_hostname="localhost"
            )
            writer.write(b"hello")
            await writer.drain()
            data = await reader.read(5)
            assert data == b"world"
            writer.close()
            await writer.wait_closed()

            await connected.wait()
            assert handshake_ok

            server.close()
            await server.wait_closed()

    async def test_tls_12_rejected(self) -> None:
        """Verify that a TLS 1.2 client cannot connect to a TLS 1.3 server."""
        with tempfile.TemporaryDirectory() as d:
            server_config = generate_test_certificates(d)
            server_ctx = build_server_ssl_context(server_config)

            # Create a TLS 1.2-only client context
            client_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            client_ctx.maximum_version = ssl.TLSVersion.TLSv1_2
            client_ctx.check_hostname = False
            client_ctx.verify_mode = ssl.CERT_NONE

            async def handle_client(
                reader: asyncio.StreamReader, writer: asyncio.StreamWriter
            ) -> None:
                pass  # pragma: no cover

            server = await asyncio.start_server(handle_client, "127.0.0.1", 0, ssl=server_ctx)
            addr = server.sockets[0].getsockname()

            with pytest.raises((ssl.SSLError, ConnectionResetError, OSError)):
                await asyncio.open_connection(
                    addr[0], addr[1], ssl=client_ctx, server_hostname="localhost"
                )

            server.close()
            await server.wait_closed()


class TestCACertLoading:
    def test_colon_separated_paths(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            generate_test_certificates(d)
            ca_path = str(Path(d) / "ca.pem")
            config = TLSConfig(ca_certificates_path=f"{ca_path}:{ca_path}")
            ctx = build_client_ssl_context(config)
            assert ctx.minimum_version == ssl.TLSVersion.TLSv1_3

    def test_nonexistent_ca_path_ignored(self) -> None:
        config = TLSConfig(ca_certificates_path="/nonexistent/ca.pem")
        # Should not raise
        ctx = build_client_ssl_context(config)
        assert ctx.minimum_version == ssl.TLSVersion.TLSv1_3

    def test_ca_directory_loading(self) -> None:
        """Cover tls.py L310-311: CA certs loaded from directory path."""
        with tempfile.TemporaryDirectory() as d:
            generate_test_certificates(d)
            # Point to the directory itself, not a file
            config = TLSConfig(ca_certificates_path=d)
            ctx = build_client_ssl_context(config)
            assert ctx.minimum_version == ssl.TLSVersion.TLSv1_3


class TestExtractPeerCn:
    def test_none_cert(self) -> None:
        assert extract_peer_cn(None) is None

    def test_no_subject(self) -> None:
        assert extract_peer_cn({}) is None

    def test_subject_not_tuple(self) -> None:
        """Cover tls.py L127: subject is not a tuple."""
        assert extract_peer_cn({"subject": "not-a-tuple"}) is None

    def test_rdn_not_tuple(self) -> None:
        """Cover tls.py L130: rdn in subject is not a tuple."""
        assert extract_peer_cn({"subject": ("not-a-tuple-rdn",)}) is None

    def test_valid_cn(self) -> None:
        cert: dict[str, object] = {
            "subject": ((("commonName", "myserver"),),),
        }
        assert extract_peer_cn(cert) == "myserver"

    def test_no_cn_in_subject(self) -> None:
        cert: dict[str, object] = {
            "subject": ((("organizationName", "Acme"),),),
        }
        assert extract_peer_cn(cert) is None


class TestExtractPeerSans:
    def test_none_cert(self) -> None:
        assert extract_peer_sans(None) == []

    def test_no_san(self) -> None:
        assert extract_peer_sans({}) == []

    def test_san_not_tuple(self) -> None:
        """Cover tls.py L147: subjectAltName is not a tuple."""
        assert extract_peer_sans({"subjectAltName": "not-a-tuple"}) == []

    def test_valid_sans(self) -> None:
        cert: dict[str, object] = {
            "subjectAltName": (("DNS", "localhost"), ("IP Address", "127.0.0.1")),
        }
        assert extract_peer_sans(cert) == ["localhost", "127.0.0.1"]


class TestResolvePassword:
    def test_str_password(self) -> None:
        """Cover tls.py L295-296: string password converted to bytes."""
        with tempfile.TemporaryDirectory() as d:
            generate_test_certificates(d)
            # Create a key file encrypted with a password
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric import ec

            key = ec.generate_private_key(ec.SECP256R1())
            key_path = Path(d) / "encrypted.key"
            key_path.write_bytes(
                key.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.BestAvailableEncryption(b"mypass"),
                )
            )
            cert_path = str(Path(d) / "server.pem")
            config = TLSConfig(
                certificate_path=cert_path,
                private_key_path=str(key_path),
                ca_certificates_path=str(Path(d) / "ca.pem"),
                key_password="mypass",
            )
            # This exercises _resolve_password with a str
            # The cert won't match the key but load_cert_chain will fail
            # for a different reason — we just need the password path hit
            with pytest.raises(ssl.SSLError):
                build_server_ssl_context(config)

    def test_bytes_password(self) -> None:
        """Cover tls.py L297: bytes password returned as-is."""
        with tempfile.TemporaryDirectory() as d:
            generate_test_certificates(d)
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric import ec

            key = ec.generate_private_key(ec.SECP256R1())
            key_path = Path(d) / "encrypted.key"
            key_path.write_bytes(
                key.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.BestAvailableEncryption(b"mypass"),
                )
            )
            config = TLSConfig(
                certificate_path=str(Path(d) / "server.pem"),
                private_key_path=str(key_path),
                ca_certificates_path=str(Path(d) / "ca.pem"),
                key_password=b"mypass",
            )
            with pytest.raises(ssl.SSLError):
                build_server_ssl_context(config)


class TestBuildServerNoChain:
    def test_server_context_no_cert(self) -> None:
        """Cover tls.py L105->108: server context without certificate."""
        config = TLSConfig()
        ctx = build_server_ssl_context(config)
        assert ctx.minimum_version == ssl.TLSVersion.TLSv1_3
