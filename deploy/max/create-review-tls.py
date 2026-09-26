"""Issue a local-only certificate; never change system trust or an existing key."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


def main():
    targets = [Path("/tls/key.pem"), Path("/tls/cert.pem"), Path("/trust/ca.pem")]
    if all(path.is_file() for path in targets):
        print("Local review certificate already exists.")
        return
    if any(path.exists() for path in targets):
        raise SystemExit("Incomplete review certificate; do not replace it automatically.")

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "joint-gateway")])
    now = datetime.now(timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=7))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("joint-gateway")]), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(key, hashes.SHA256())
    )
    for path in targets:
        path.parent.mkdir(parents=True, exist_ok=True)
    with targets[0].open("xb") as stream:
        stream.write(key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ))
    targets[0].chmod(0o600)
    content = certificate.public_bytes(serialization.Encoding.PEM)
    for path in targets[1:]:
        with path.open("xb") as stream:
            stream.write(content)
    print("Local review certificate created; TLS verification remains enabled.")


if __name__ == "__main__":
    main()
