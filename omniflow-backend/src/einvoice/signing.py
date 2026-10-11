"""Invoice hash (chain) and XAdES-style enveloped ECDSA signature, ZATCA profile.

Canonicalisation: inclusive C14N (lxml `c14n`). ZATCA names C14N 1.1; the two only differ for inherited xml:* attributes, which these
documents do not contain. Quirks reproduced from ZATCA's published samples: the invoice reference digest is base64(SHA-256 bytes);
the SignedProperties digest and the certificate digest are base64 of the *hex* SHA-256 string.
NOT validated against ZATCA's SDK in this repository — see src/einvoice/__init__.py.
"""
from __future__ import annotations

import base64
import copy
import hashlib
from datetime import datetime, timezone

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from lxml import etree

from src.einvoice import tlv
from src.einvoice.ubl import CAC, CBC, EXT

DS = "http://www.w3.org/2000/09/xmldsig#"
XADES = "http://uri.etsi.org/01903/v1.3.2#"
SIG = "urn:oasis:names:specification:ubl:schema:xsd:CommonSignatureComponents-2"
SAC = "urn:oasis:names:specification:ubl:schema:xsd:SignatureAggregateComponents-2"
SBC = "urn:oasis:names:specification:ubl:schema:xsd:SignatureBasicComponents-2"
ALG_C14N = "http://www.w3.org/2006/12/xml-c14n11"
ALG_ECDSA = "http://www.w3.org/2001/04/xmldsig-more#ecdsa-sha256"
ALG_SHA256 = "http://www.w3.org/2001/04/xmlenc#sha256"
SIG_ID, SP_ID = "signature", "xadesSignedProperties"


def _c14n(el) -> bytes:
    return etree.tostring(el, method="c14n", exclusive=False, with_comments=False)


def _strip_signed_parts(root):
    """The three XPath transforms of the invoice reference: no UBLExtensions, no cac:Signature, no QR reference."""
    clone = copy.deepcopy(root)
    for el in clone.findall(EXT + "UBLExtensions"):
        clone.remove(el)
    for el in clone.findall(CAC + "Signature"):
        clone.remove(el)
    for ref in clone.findall(CAC + "AdditionalDocumentReference"):
        if ref.findtext(CBC + "ID") == "QR":
            clone.remove(ref)
    return clone


def invoice_hash(root) -> bytes:
    """SHA-256 of the canonical form of the invoice without extensions / signature / QR. This is also what the next invoice's PIH chains to."""
    return hashlib.sha256(_c14n(_strip_signed_parts(root))).digest()


def invoice_hash_b64(root) -> str:
    return base64.b64encode(invoice_hash(root)).decode()


def _hexdigest_b64(data: bytes) -> str:
    return base64.b64encode(hashlib.sha256(data).hexdigest().encode()).decode()


def _ds(tag: str, **attrs):
    return etree.Element("{%s}%s" % (DS, tag), **attrs)


def _add(parent, ns: str, tag: str, text: str | None = None, **attrs):
    el = etree.SubElement(parent, "{%s}%s" % (ns, tag), **attrs)
    if text is not None:
        el.text = text
    return el


def _issuer_name(cert: x509.Certificate) -> str:
    return cert.issuer.rfc4514_string()


def sign_invoice(root, private_key: ec.EllipticCurvePrivateKey, cert_der: bytes, *, signing_time: datetime | None = None,
                 qr_fields: dict | None = None, simplified: bool = False) -> dict:
    """Sign in place: inserts UBLExtensions (first child) and, if `qr_fields` is given, the QR reference. Returns hash/signature/qr for storage."""
    signing_time = (signing_time or datetime.now(timezone.utc)).astimezone(timezone.utc)
    cert = x509.load_der_x509_certificate(cert_der)
    inv_hash = invoice_hash(root)
    inv_hash_b64 = base64.b64encode(inv_hash).decode()

    ext_root = etree.Element(EXT + "UBLExtensions", nsmap={"ext": EXT.strip("{}")})
    ext = etree.SubElement(ext_root, EXT + "UBLExtension")
    etree.SubElement(ext, EXT + "ExtensionURI").text = "urn:oasis:names:specification:ubl:dsig:enveloped:xades"
    content = etree.SubElement(ext, EXT + "ExtensionContent")
    docsig = etree.SubElement(content, "{%s}UBLDocumentSignatures" % SIG, nsmap={"sig": SIG, "sac": SAC, "sbc": SBC})
    info = etree.SubElement(docsig, "{%s}SignatureInformation" % SAC)
    etree.SubElement(info, CBC + "ID").text = "urn:oasis:names:specification:ubl:signature:1"
    etree.SubElement(info, "{%s}ReferencedSignatureID" % SBC).text = "urn:oasis:names:specification:ubl:signature:Invoice"
    signature = etree.SubElement(info, "{%s}Signature" % DS, Id=SIG_ID, nsmap={"ds": DS})
    signed_info = _add(signature, DS, "SignedInfo")
    _add(signed_info, DS, "CanonicalizationMethod", Algorithm=ALG_C14N)
    _add(signed_info, DS, "SignatureMethod", Algorithm=ALG_ECDSA)
    ref1 = _add(signed_info, DS, "Reference", Id="invoiceSignedData", URI="")
    transforms = _add(ref1, DS, "Transforms")
    for xp in ("not(//ancestor-or-self::ext:UBLExtensions)", "not(//ancestor-or-self::cac:Signature)",
               "not(//ancestor-or-self::cac:AdditionalDocumentReference[cbc:ID='QR'])"):
        t = _add(transforms, DS, "Transform", Algorithm="http://www.w3.org/TR/1999/REC-xpath-19991116")
        _add(t, DS, "XPath", xp)
    _add(transforms, DS, "Transform", Algorithm=ALG_C14N)
    _add(ref1, DS, "DigestMethod", Algorithm=ALG_SHA256)
    _add(ref1, DS, "DigestValue", inv_hash_b64)
    ref2 = _add(signed_info, DS, "Reference", Type="http://www.w3.org/2000/09/xmldsig#SignatureProperties", URI="#" + SP_ID)
    _add(ref2, DS, "DigestMethod", Algorithm=ALG_SHA256)
    sp_digest_el = _add(ref2, DS, "DigestValue", "PLACEHOLDER")
    sig_value = _add(signature, DS, "SignatureValue", "PLACEHOLDER")
    key_info = _add(signature, DS, "KeyInfo")
    _add(_add(key_info, DS, "X509Data"), DS, "X509Certificate", base64.b64encode(cert_der).decode())
    obj = _add(signature, DS, "Object")
    qp = etree.SubElement(obj, "{%s}QualifyingProperties" % XADES, Target="#" + SIG_ID, nsmap={"xades": XADES})
    sp = _add(qp, XADES, "SignedProperties", Id=SP_ID)
    ssp = _add(sp, XADES, "SignedSignatureProperties")
    _add(ssp, XADES, "SigningTime", signing_time.strftime("%Y-%m-%dT%H:%M:%SZ"))
    cert_el = _add(_add(ssp, XADES, "SigningCertificate"), XADES, "Cert")
    cd = _add(cert_el, XADES, "CertDigest")
    _add(cd, DS, "DigestMethod", Algorithm=ALG_SHA256)
    _add(cd, DS, "DigestValue", _hexdigest_b64(cert_der))
    iss = _add(cert_el, XADES, "IssuerSerial")
    _add(iss, DS, "X509IssuerName", _issuer_name(cert))
    _add(iss, DS, "X509SerialNumber", str(cert.serial_number))

    root.insert(0, ext_root)                                   # in place, so canonicalisation sees the real in-scope namespaces
    sp_digest_el.text = _hexdigest_b64(_c14n(sp))
    der_sig = private_key.sign(_c14n(signed_info), ec.ECDSA(hashes.SHA256()))
    sig_b64 = base64.b64encode(der_sig).decode()
    sig_value.text = sig_b64

    qr = None
    if qr_fields is not None:
        pub_der = cert.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        qr = tlv.build_qr(invoice_hash_b64=inv_hash_b64, signature_b64=sig_b64, public_key_der=pub_der,
                          cert_signature=cert.signature if simplified else None, **qr_fields)
        pih_ref = next(r for r in root.findall(CAC + "AdditionalDocumentReference") if r.findtext(CBC + "ID") == "PIH")
        qref = etree.Element(CAC + "AdditionalDocumentReference")
        etree.SubElement(qref, CBC + "ID").text = "QR"
        emb = etree.SubElement(etree.SubElement(qref, CAC + "Attachment"), CBC + "EmbeddedDocumentBinaryObject", mimeCode="text/plain")
        emb.text = qr
        pih_ref.addnext(qref)
    return {"invoice_hash": inv_hash_b64, "signature": sig_b64, "qr": qr}


class SignatureInvalid(Exception):
    pass


def verify_invoice(root) -> bool:
    """Re-derive both digests and check the ECDSA signature with the certificate embedded in the document. Raises SignatureInvalid."""
    ns = {"ds": DS, "xades": XADES, "ext": EXT.strip("{}")}
    sig = root.find(".//ds:Signature", ns)
    if sig is None:
        raise SignatureInvalid("no signature")
    signed_info = sig.find("ds:SignedInfo", ns)
    refs = signed_info.findall("ds:Reference", ns)
    if len(refs) != 2:
        raise SignatureInvalid("unexpected references")
    if refs[0].findtext("ds:DigestValue", namespaces=ns) != invoice_hash_b64(root):
        raise SignatureInvalid("invoice digest mismatch (document changed after signing)")
    sp = sig.find(".//xades:SignedProperties", ns)
    if refs[1].findtext("ds:DigestValue", namespaces=ns) != _hexdigest_b64(_c14n(sp)):
        raise SignatureInvalid("signed properties digest mismatch")
    cert_der = base64.b64decode(sig.findtext(".//ds:X509Certificate", namespaces=ns))
    cert = x509.load_der_x509_certificate(cert_der)
    if sp.findtext(".//xades:CertDigest/ds:DigestValue", namespaces=ns) != _hexdigest_b64(cert_der):
        raise SignatureInvalid("certificate digest mismatch")
    try:
        cert.public_key().verify(base64.b64decode(sig.findtext("ds:SignatureValue", namespaces=ns)), _c14n(signed_info), ec.ECDSA(hashes.SHA256()))
    except InvalidSignature as exc:
        raise SignatureInvalid("ECDSA signature does not verify") from exc
    return True
