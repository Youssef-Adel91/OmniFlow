"""Key + CSR generation for ZATCA onboarding, and a DEV-ONLY self-signed certificate used by tests.

Real certificates (Compliance / Production CSID) are issued by ZATCA after `build_csr` output is submitted with an OTP from the Fatoora
portal. Private keys are generated here and must be stored encrypted (see service.py) — never in Git, never logged.
"""
from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.name import _ASN1Type
from cryptography.x509.oid import NameOID, ObjectIdentifier

_UTF8 = _ASN1Type.UTF8String

OID_TEMPLATE = ObjectIdentifier("1.3.6.1.4.1.311.20.2")      # certificate template name
OID_UID = ObjectIdentifier("0.9.2342.19200300.100.1.1")
OID_REGISTERED_ADDRESS = ObjectIdentifier("2.5.4.26")
TEMPLATES = {"sandbox": "TSTZATCA-Code-Signing", "simulation": "PREZATCA-Code-Signing", "production": "ZATCA-Code-Signing"}


def generate_key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256K1())


def key_to_pem(key: ec.EllipticCurvePrivateKey) -> bytes:
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())


def key_from_pem(pem: bytes) -> ec.EllipticCurvePrivateKey:
    k = serialization.load_pem_private_key(pem, password=None)
    if not isinstance(k, ec.EllipticCurvePrivateKey):
        raise ValueError("not an EC key")
    return k


def build_csr(key: ec.EllipticCurvePrivateKey, *, env: str, common_name: str, org: str, org_unit: str, vat_number: str, unit_serial: str,
              invoice_types: str = "1100", address: str, business_category: str, country: str = "SA") -> bytes:
    """PEM CSR in ZATCA's format. `unit_serial` is the EGS serial: 1-<solution>|2-<model>|3-<unique id>. `invoice_types` e.g. 1100 (B2B+B2C)."""
    if env not in TEMPLATES:
        raise ValueError("env must be sandbox | simulation | production")
    if len(vat_number) != 15 or not vat_number.isdigit() or not vat_number.startswith("3") or not vat_number.endswith("3"):
        raise ValueError("VAT number must be 15 digits starting and ending with 3")
    u = lambda oid, v: x509.NameAttribute(oid, v, _type=_UTF8)       # noqa: E731 - "|" (unit serial) and Arabic text are not PrintableString
    subject = x509.Name([x509.NameAttribute(NameOID.COUNTRY_NAME, country), u(NameOID.ORGANIZATIONAL_UNIT_NAME, org_unit),
                         u(NameOID.ORGANIZATION_NAME, org), u(NameOID.COMMON_NAME, common_name)])
    san_dir = x509.Name([u(NameOID.SERIAL_NUMBER, unit_serial), u(OID_UID, vat_number), u(NameOID.TITLE, invoice_types),
                         u(OID_REGISTERED_ADDRESS, address), u(NameOID.BUSINESS_CATEGORY, business_category)])
    template = x509.UnrecognizedExtension(OID_TEMPLATE, bytes([0x0C, len(TEMPLATES[env])]) + TEMPLATES[env].encode())   # DER UTF8String
    csr = (x509.CertificateSigningRequestBuilder().subject_name(subject)
           .add_extension(template, critical=False)
           .add_extension(x509.SubjectAlternativeName([x509.DirectoryName(san_dir)]), critical=False)
           .sign(key, hashes.SHA256()))
    return csr.public_bytes(serialization.Encoding.PEM)


def csr_request_body(csr_pem: bytes) -> str:
    """ZATCA expects the base64 of the PEM text."""
    return base64.b64encode(csr_pem).decode()


def dev_self_signed_cert(key: ec.EllipticCurvePrivateKey, *, cn: str = "OmniFlow DEV ONLY - NOT A ZATCA CERTIFICATE") -> bytes:
    """DER certificate signed by itself — exists ONLY so the signing code can be exercised in tests. ZATCA will never accept it."""
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn), x509.NameAttribute(NameOID.COUNTRY_NAME, "SA")])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=30)).sign(key, hashes.SHA256()))
    return cert.public_bytes(serialization.Encoding.DER)
