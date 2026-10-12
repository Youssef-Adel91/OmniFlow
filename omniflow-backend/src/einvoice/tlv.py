"""QR payload: TLV (tag, length, value) records, base64 of the concatenation.

Tags 1-5 are the classic five (seller, VAT number, timestamp, total with VAT, VAT amount); 6-9 are the Phase-2 additions:
6 invoice hash, 7 ECDSA signature, 8 public key (DER), 9 the CSID certificate's signature (simplified invoices only).
"""
from __future__ import annotations

import base64


def tlv(tag: int, value: bytes) -> bytes:
    if not 1 <= tag <= 255:
        raise ValueError("tag out of range")
    if len(value) > 255:      # ZATCA QR values fit one length byte; longer values (signature/key) use the same single-byte rule in the spec samples
        if len(value) > 65535:
            raise ValueError("value too long")
    n = len(value)
    if n < 128:
        return bytes([tag, n]) + value
    # BER-style length for >127 bytes (public key / signature are 70-91 bytes, the cert signature can be longer)
    if n < 256:
        return bytes([tag, 0x81, n]) + value
    return bytes([tag, 0x82, n >> 8, n & 0xFF]) + value


def build_qr(*, seller_name: str, vat_number: str, timestamp: str, total: str, vat_total: str,
             invoice_hash_b64: str | None = None, signature_b64: str | None = None, public_key_der: bytes | None = None,
             cert_signature: bytes | None = None) -> str:
    out = tlv(1, seller_name.encode()) + tlv(2, vat_number.encode()) + tlv(3, timestamp.encode()) + tlv(4, total.encode()) + tlv(5, vat_total.encode())
    if invoice_hash_b64 is not None:
        out += tlv(6, invoice_hash_b64.encode())
    if signature_b64 is not None:
        out += tlv(7, base64.b64decode(signature_b64))
    if public_key_der is not None:
        out += tlv(8, public_key_der)
    if cert_signature is not None:
        out += tlv(9, cert_signature)
    return base64.b64encode(out).decode()


def parse_qr(b64: str) -> dict[int, bytes]:
    """Inverse of build_qr (for tests and diagnostics)."""
    raw, i, out = base64.b64decode(b64), 0, {}
    while i < len(raw):
        tag, n = raw[i], raw[i + 1]
        i += 2
        if n == 0x81:
            n, i = raw[i], i + 1
        elif n == 0x82:
            n, i = (raw[i] << 8) | raw[i + 1], i + 2
        out[tag] = raw[i:i + n]
        i += n
    return out
