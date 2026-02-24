"""TLS 1.3 configuration and SSL context builders.

Provides ``TLSConfig`` for declaring certificate paths and helper functions
for constructing ``ssl.SSLContext`` instances that enforce TLS 1.3 minimum.

System CA trust is deliberately excluded — only explicitly configured CA
certificates are loaded.

Uses the ``cryptography`` library for test certificate generation.
"""

from __future__ import annotations

import datetime
import ipaddress
import ssl
from dataclasses import dataclass
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

__all__ = [
    "TLSConfig",
    "build_client_ssl_context",
    "build_server_ssl_context",
    "extract_peer_cn",
    "extract_peer_sans",
    "generate_test_certificates",
]


@dataclass(frozen=True, slots=True)
class TLSConfig:
    """TLS 1.3 configuration for Haystack client and server.

    :param certificate_path: Path to PEM certificate file.
    :param private_key_path: Path to PEM private key file.
    :param ca_certificates_path: Colon-separated paths to CA PEM files or directories.
    :param key_password: Passphrase for the private key (bytes or str).
    """

    certificate_path: str | None = None
    """Path to PEM certificate file."""

    private_key_path: str | None = None
    """Path to PEM private key file."""

    ca_certificates_path: str | None = None
    """Colon-separated paths to CA PEM files or directories."""

    key_password: bytes | str | None = None
    """Passphrase for the private key (bytes or str)."""

    def __repr__(self) -> str:
        """Redact sensitive fields in repr output."""
        key_path = "<REDACTED>" if self.private_key_path else None
        pw = "<REDACTED>" if self.key_password else None
        return (
            f"TLSConfig(certificate_path={self.certificate_path!r}, "
            f"private_key_path={key_path!r}, "
            f"ca_certificates_path={self.ca_certificates_path!r}, "
            f"key_password={pw!r})"
        )


# ---------------------------------------------------------------------------
# SSL context builders
# ---------------------------------------------------------------------------


def build_client_ssl_context(config: TLSConfig) -> ssl.SSLContext:
    """Build a TLS 1.3 client SSL context.

    ``PROTOCOL_TLS_CLIENT`` enables hostname verification and requires
    server certificates by default.

    :param config: TLS configuration with certificate paths.
    :returns: Configured :class:`ssl.SSLContext`.
    """
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_3
    ctx.verify_flags |= ssl.VERIFY_X509_STRICT
    if config.certificate_path and config.private_key_path:
        password = _resolve_password(config.key_password)
        ctx.load_cert_chain(config.certificate_path, config.private_key_path, password=password)
    _load_ca_certs(ctx, config)
    return ctx


def build_server_ssl_context(config: TLSConfig) -> ssl.SSLContext:
    """Build a TLS 1.3 server SSL context with mutual authentication.

    ``verify_mode = CERT_REQUIRED`` enforces client certificate verification.

    :param config: TLS configuration with certificate paths.
    :returns: Configured :class:`ssl.SSLContext`.
    """
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_3
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.verify_flags |= ssl.VERIFY_X509_STRICT
    if config.certificate_path and config.private_key_path:
        password = _resolve_password(config.key_password)
        ctx.load_cert_chain(config.certificate_path, config.private_key_path, password=password)
    _load_ca_certs(ctx, config)
    return ctx


# ---------------------------------------------------------------------------
# Peer certificate identity extraction
# ---------------------------------------------------------------------------


def extract_peer_cn(peercert: dict[str, object] | None) -> str | None:
    """Extract the Common Name (CN) from a peer certificate dict.

    :param peercert: Certificate dict as returned by ``ssl.SSLSocket.getpeercert()``.
    :returns: The CN string, or *None* if not present.
    """
    if peercert is None:
        return None
    subject = peercert.get("subject")
    if not isinstance(subject, tuple):
        return None
    for rdn in subject:
        if not isinstance(rdn, tuple):
            continue
        for attr in rdn:
            if isinstance(attr, tuple) and len(attr) == 2 and attr[0] == "commonName":
                return str(attr[1])
    return None


def extract_peer_sans(peercert: dict[str, object] | None) -> list[str]:
    """Extract Subject Alternative Names from a peer certificate dict.

    :param peercert: Certificate dict as returned by ``ssl.SSLSocket.getpeercert()``.
    :returns: List of SAN values (DNS names and IP addresses).
    """
    if peercert is None:
        return []
    san = peercert.get("subjectAltName")
    if not isinstance(san, tuple):
        return []
    return [str(value) for _kind, value in san if isinstance(value, str)]


# ---------------------------------------------------------------------------
# Test certificate generation
# ---------------------------------------------------------------------------


def generate_test_certificates(directory: str | Path) -> TLSConfig:
    """Generate a self-signed CA and server/client certificates for testing.

    Uses EC P-256 keys with SHA-256 signatures. Certificates are valid for
    one year from generation time. Writes PEM files to *directory*:

    - ``ca.pem`` — CA certificate
    - ``server.pem`` / ``server.key`` — Server certificate and private key
    - ``client.pem`` / ``client.key`` — Client certificate and private key

    :param directory: Directory to write certificate files into.
    :returns: A ``TLSConfig`` pointing to the server certificate and CA.
    """
    out = Path(directory)
    out.mkdir(parents=True, exist_ok=True)

    now = datetime.datetime.now(datetime.UTC)
    validity = datetime.timedelta(days=365)

    # --- CA ---
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Haystack Test CA")])
    ca_ski = x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key())
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + validity)
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                key_cert_sign=True,
                crl_sign=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(ca_ski, critical=False)
        .sign(ca_key, hashes.SHA256())
    )

    ca_aki = x509.AuthorityKeyIdentifier.from_issuer_subject_key_identifier(ca_ski)
    sans = x509.SubjectAlternativeName(
        [
            x509.DNSName("localhost"),
            x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
            x509.IPAddress(ipaddress.IPv6Address("::1")),
        ]
    )

    # --- Server cert ---
    server_key = ec.generate_private_key(ec.SECP256R1())
    server_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Haystack Test Server")])
    server_cert = _build_device_cert(
        server_name, server_key, ca_name, ca_key, ca_aki, sans, now, validity
    )

    # --- Client cert ---
    client_key = ec.generate_private_key(ec.SECP256R1())
    client_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Haystack Test Client")])
    client_cert = _build_device_cert(
        client_name, client_key, ca_name, ca_key, ca_aki, sans, now, validity
    )

    # --- Write files ---
    _write_cert(out / "ca.pem", ca_cert)
    _write_cert(out / "server.pem", server_cert)
    _write_key(out / "server.key", server_key)
    _write_cert(out / "client.pem", client_cert)
    _write_key(out / "client.key", client_key)

    return TLSConfig(
        certificate_path=str(out / "server.pem"),
        private_key_path=str(out / "server.key"),
        ca_certificates_path=str(out / "ca.pem"),
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_device_cert(
    subject: x509.Name,
    key: ec.EllipticCurvePrivateKey,
    issuer: x509.Name,
    ca_key: ec.EllipticCurvePrivateKey,
    ca_aki: x509.AuthorityKeyIdentifier,
    sans: x509.SubjectAlternativeName,
    now: datetime.datetime,
    validity: datetime.timedelta,
) -> x509.Certificate:
    """Build and sign a device certificate."""
    return (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + validity)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(sans, critical=False)
        .add_extension(ca_aki, critical=False)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(ca_key, hashes.SHA256())
    )


def _write_cert(path: Path, cert: x509.Certificate) -> None:
    """Write a certificate as PEM."""
    path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))


def _write_key(path: Path, key: ec.EllipticCurvePrivateKey) -> None:
    """Write a private key as PEM (unencrypted)."""
    path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )


def _resolve_password(password: bytes | str | None) -> bytes | None:
    """Convert password to bytes if needed."""
    if password is None:
        return None
    if isinstance(password, str):
        return password.encode()
    return password


def _load_ca_certs(ctx: ssl.SSLContext, config: TLSConfig) -> None:
    """Load CA certificates from configured paths. No system CA trust."""
    if not config.ca_certificates_path:
        return
    for path_str in config.ca_certificates_path.split(":"):
        p = Path(path_str.strip())
        if not p.exists():
            continue
        if p.is_file():
            ctx.load_verify_locations(cafile=str(p))
        elif p.is_dir():
            ctx.load_verify_locations(capath=str(p))
