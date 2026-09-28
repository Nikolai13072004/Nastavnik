"""Real loopback SMTP/TLS sockets, ephemeral certificates, no external delivery."""
from datetime import datetime, timedelta, timezone
from email import policy
from email.parser import BytesParser
import socket
import ssl
import threading

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
import pytest

import config
from src import email_service


@pytest.fixture
def local_smtp(tmp_path, monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1))
            .not_valid_after(now + timedelta(hours=1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .sign(key, hashes.SHA256()))
    cert_path = tmp_path / "synthetic-cert.pem"
    key_path = tmp_path / "synthetic-key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                         serialization.PrivateFormat.PKCS8,
                                         serialization.NoEncryption()))
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(cert_path, key_path)
    original_context = ssl.create_default_context
    received = []
    errors = []
    threads = []
    listeners = []

    def start(*, implicit, trusted, hostname="localhost"):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(5)
        listeners.append(listener)

        def serve():
            connection = None
            stream = None
            try:
                connection, _ = listener.accept()
                connection.settimeout(5)
                encrypted = implicit
                if implicit:
                    connection = server_context.wrap_socket(connection, server_side=True)
                stream = connection.makefile("rwb")

                def reply(data):
                    stream.write(data)
                    stream.flush()

                reply(b"220 localhost synthetic SMTP\r\n")
                while line := stream.readline():
                    command = line.split(b" ", 1)[0].strip().upper()
                    if command in {b"EHLO", b"HELO"}:
                        reply(b"250 localhost\r\n" if encrypted else b"250-localhost\r\n250 STARTTLS\r\n")
                    elif command == b"STARTTLS":
                        reply(b"220 start TLS\r\n")
                        stream.close()
                        stream = None
                        connection = server_context.wrap_socket(connection, server_side=True)
                        stream = connection.makefile("rwb")
                        encrypted = True
                    elif command in {b"MAIL", b"RCPT", b"RSET"}:
                        reply(b"250 accepted\r\n")
                    elif command == b"DATA":
                        assert encrypted, "mail must never arrive before TLS"
                        reply(b"354 send data\r\n")
                        lines = []
                        while data := stream.readline():
                            if data == b".\r\n":
                                break
                            lines.append(data[1:] if data.startswith(b"..") else data)
                        received.append(BytesParser(policy=policy.default).parsebytes(b"".join(lines)))
                        reply(b"250 stored only in test memory\r\n")
                    elif command == b"QUIT":
                        reply(b"221 bye\r\n")
                        break
                    else:
                        reply(b"502 unsupported\r\n")
            except (OSError, AssertionError) as exc:
                errors.append(exc)
            finally:
                if stream is not None:
                    stream.close()
                if connection is not None:
                    connection.close()

        thread = threading.Thread(target=serve, daemon=True)
        threads.append(thread)
        thread.start()
        monkeypatch.setattr(config, "EMAIL_BACKEND", "smtp")
        monkeypatch.setattr(config, "EMAIL_FROM", "sender@example.com")
        monkeypatch.setattr(config, "SMTP_HOST", hostname)
        monkeypatch.setattr(config, "SMTP_PORT", listener.getsockname()[1])
        monkeypatch.setattr(config, "SMTP_USER", "")
        monkeypatch.setattr(config, "SMTP_USE_SSL", implicit)
        monkeypatch.setattr(config, "SMTP_USE_TLS", True)
        if trusted:
            def trusted_context():
                context = original_context()
                context.load_verify_locations(cafile=str(cert_path))
                return context
            monkeypatch.setattr(email_service.ssl, "create_default_context", trusted_context)
        return received, errors, thread

    yield start
    for listener in listeners:
        listener.close()
    for thread in threads:
        thread.join(timeout=6)
        assert not thread.is_alive(), "synthetic SMTP listener failed to stop"


@pytest.mark.parametrize("implicit", [False, True])
def test_real_smtp_accepts_trusted_certificate(local_smtp, implicit):
    messages, errors, thread = local_smtp(implicit=implicit, trusted=True)
    email_service.send_email("recipient@example.com", "Подтверждение", "synthetic-token")
    thread.join(timeout=6)
    assert not errors
    assert len(messages) == 1
    assert messages[0]["Subject"] == "Подтверждение"
    assert messages[0].get_content().strip() == "synthetic-token"


@pytest.mark.parametrize("implicit", [False, True])
@pytest.mark.parametrize("reason", ["untrusted", "hostname"])
def test_real_smtp_rejects_invalid_certificate(
    local_smtp, implicit, reason, vedomo_caplog
):
    messages, _, thread = local_smtp(implicit=implicit, trusted=reason == "hostname",
                                    hostname="127.0.0.1" if reason == "hostname" else "localhost")
    email_service.send_email("recipient@example.com", "subject", "synthetic-token")
    thread.join(timeout=6)
    assert not messages
    assert "reason=SSLCertVerificationError" in vedomo_caplog.text
    assert "synthetic-token" not in vedomo_caplog.text
