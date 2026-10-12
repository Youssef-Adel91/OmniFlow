"""ZATCA e-invoicing module: QR TLV, UBL XML, hash chain, XAdES-style signature, CSR, API client behaviour, storage, submission state machine.

WHAT THIS PROVES: our own invariants (structure, chain integrity, signature round-trip, tamper detection, atomic ICV, retry/dead-letter logic, RLS).
WHAT IT DOES NOT PROVE: acceptance by ZATCA. No ZATCA SDK/validator, official sample files or ZATCA server was available; the certificate used here is
a DEV-ONLY self-signed one. See docs/morning-verification.md for the checks to run with the official SDK.
"""
import asyncio
import base64
import hashlib
import json
import os
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from lxml import etree

from src.einvoice import certs, signing, tlv, ubl
from src.einvoice.client import Credentials, EInvoiceError, ZatcaClient, base_url
from src.shared.core.config import get_settings

NS = {"cbc": ubl.NS["cbc"], "cac": ubl.NS["cac"], "ext": ubl.NS["ext"], "ds": signing.DS}
UTC = timezone.utc
SELLER = ubl.Party(name="شركة أومني فلو", vat_number="399999999900003", crn="1010010000", street="شارع الملك فهد", building="1234", district="العليا", city="الرياض", postal_zone="12222")
BUYER = ubl.Party(name="مكتب عقارات النخبة", vat_number="310000000000003", crn="1010020000", street="طريق الأمير", building="5678", district="الملقا", city="الرياض", postal_zone="13524")


def make(**kw) -> ubl.InvoiceData:
    d = dict(number="INV-2026-000001", uuid=str(uuid.uuid4()), issued_at=datetime(2026, 10, 11, 9, 30, 15, tzinfo=UTC), type_code="388", subtype="0100000",
             seller=SELLER, buyer=BUYER, lines=[ubl.Line("اشتراك OmniFlow — الاحترافية (شهري)", 1, 79900)], icv=1, pih=ubl.PIH_FIRST)
    d.update(kw)
    return ubl.InvoiceData(**d)


def signed(inv=None, key=None, simplified=False):
    inv = inv or make()
    key = key or certs.generate_key()
    cert = certs.dev_self_signed_cert(key)
    root = ubl.build_xml(inv)
    t = ubl.totals(inv)
    res = signing.sign_invoice(root, key, cert, qr_fields=dict(seller_name=inv.seller.name, vat_number=inv.seller.vat_number, timestamp="2026-10-11T09:30:15Z",
                                                                  total=ubl.money(t["incl"]), vat_total=ubl.money(t["tax"])), simplified=simplified)
    return root, res, key, cert


class TlvTests(unittest.TestCase):
    def test_round_trip_and_tag_order(self):
        qr = tlv.build_qr(seller_name="شركة", vat_number="399999999900003", timestamp="2026-10-11T09:30:15Z", total="918.85", vat_total="119.85")
        parsed = tlv.parse_qr(qr)
        self.assertEqual([k for k in parsed], [1, 2, 3, 4, 5])
        self.assertEqual((parsed[1].decode(), parsed[2].decode(), parsed[4].decode(), parsed[5].decode()), ("شركة", "399999999900003", "918.85", "119.85"))

    def test_phase_two_tags_and_long_values_use_ber_lengths(self):
        sig = base64.b64encode(os.urandom(71)).decode()
        qr = tlv.build_qr(seller_name="x", vat_number="399999999900003", timestamp="t", total="1", vat_total="0", invoice_hash_b64="h" * 44, signature_b64=sig,
                          public_key_der=os.urandom(88), cert_signature=os.urandom(200))
        p = tlv.parse_qr(qr)
        self.assertEqual(sorted(p), [1, 2, 3, 4, 5, 6, 7, 8, 9])
        self.assertEqual((len(p[7]), len(p[8]), len(p[9])), (71, 88, 200))
        with self.assertRaises(ValueError):
            tlv.tlv(0, b"x")


class UblTests(unittest.TestCase):
    def test_first_pih_is_the_documented_constant(self):
        self.assertEqual(ubl.PIH_FIRST, base64.b64encode(hashlib.sha256(b"0").hexdigest().encode()).decode())

    def test_totals_round_per_line_like_the_validator(self):
        t = ubl.totals(make())
        self.assertEqual((ubl.money(t["ext"]), ubl.money(t["tax"]), ubl.money(t["incl"])), ("799.00", "119.85", "918.85"))
        t = ubl.totals(make(lines=[ubl.Line("a", 3, 333), ubl.Line("b", 1, 1)]))                     # 9.99 -> 1.4985 -> 1.50 ; 0.01 -> 0.0015 -> 0.00
        self.assertEqual((ubl.money(t["ext"]), ubl.money(t["tax"])), ("10.00", "1.50"))

    def test_required_zatca_elements_are_present(self):
        root = ubl.build_xml(make())
        f = lambda x: root.find(x, NS)  # noqa: E731
        self.assertEqual(f("cbc:ProfileID").text, "reporting:1.0")
        self.assertEqual((f("cbc:InvoiceTypeCode").text, f("cbc:InvoiceTypeCode").get("name")), ("388", "0100000"))
        self.assertEqual((f("cbc:IssueDate").text, f("cbc:IssueTime").text), ("2026-10-11", "12:30:15"))               # stored UTC, shown in Riyadh time
        refs = {r.findtext("cbc:ID", namespaces=NS): r for r in root.findall("cac:AdditionalDocumentReference", NS)}
        self.assertEqual(refs["ICV"].findtext("cbc:UUID", namespaces=NS), "1")
        self.assertEqual(refs["PIH"].findtext(".//cbc:EmbeddedDocumentBinaryObject", namespaces=NS), ubl.PIH_FIRST)
        self.assertEqual(f("cac:AccountingSupplierParty//cac:PartyTaxScheme/cbc:CompanyID").text, "399999999900003")
        self.assertEqual(len(root.findall("cac:TaxTotal", NS)), 2)                                                       # the two TaxTotal blocks ZATCA requires
        self.assertEqual(f("cac:LegalMonetaryTotal/cbc:PayableAmount").text, "918.85")
        self.assertEqual(f("cac:LegalMonetaryTotal/cbc:PayableAmount").get("currencyID"), "SAR")
        self.assertEqual(f("cac:InvoiceLine/cac:Item/cac:ClassifiedTaxCategory/cbc:Percent").text, "15.00")
        self.assertIsNotNone(f("cac:Signature"))

    def test_credit_note_carries_reason_and_reference(self):
        root = ubl.build_xml(make(type_code="381", reason="استرداد خلال فترة الضمان", billing_reference="INV-2026-000001"))
        self.assertEqual(root.find("cbc:InvoiceTypeCode", NS).text, "381")
        self.assertEqual(root.findtext("cac:BillingReference/cac:InvoiceDocumentReference/cbc:ID", namespaces=NS), "INV-2026-000001")
        self.assertEqual(root.findtext("cac:PaymentMeans/cbc:InstructionNote", namespaces=NS), "استرداد خلال فترة الضمان")

    def test_business_rules_reject_incomplete_documents(self):
        bad = {
            "no seller VAT": make(seller=ubl.Party(name="x", vat_number="")),
            "short seller VAT": make(seller=ubl.Party(name="x", vat_number="123")),
            "B2B without buyer VAT": make(buyer=ubl.Party(name="x")),
            "credit note without reason": make(type_code="381", billing_reference="INV-1"),
            "credit note without reference": make(type_code="381", reason="r"),
            "no lines": make(lines=[]),
            "bad uuid": make(uuid="not-a-uuid"),
            "icv 0": make(icv=0),
            "unknown type": make(type_code="999"),
        }
        for label, inv in bad.items():
            with self.subTest(label), self.assertRaises(ubl.InvalidInvoice):
                ubl.build_xml(inv)
        ubl.build_xml(make(subtype="0200000", buyer=ubl.Party(name="")))                                             # simplified (B2C) may omit the buyer


class SigningTests(unittest.TestCase):
    def test_signature_verifies_and_survives_reserialisation(self):
        root, res, _, _ = signed()
        self.assertTrue(signing.verify_invoice(root))
        again = etree.fromstring(etree.tostring(root, xml_declaration=True, encoding="UTF-8"))
        self.assertTrue(signing.verify_invoice(again))
        self.assertEqual(signing.invoice_hash_b64(again), res["invoice_hash"])

    def test_any_change_after_signing_is_detected(self):
        for xpath, value in (("cac:LegalMonetaryTotal/cbc:PayableAmount", "1.00"), ("cbc:UUID", str(uuid.uuid4())), ("cbc:ID", "INV-HACKED"),
                             ("cac:AccountingCustomerParty//cbc:RegistrationName", "غير الموقّع")):
            root, _, _, _ = signed()
            copy = etree.fromstring(etree.tostring(root))
            copy.find(xpath, NS).text = value
            with self.subTest(xpath), self.assertRaises(signing.SignatureInvalid):
                signing.verify_invoice(copy)

    def test_chain_fields_are_covered_by_the_signature(self):
        root, _, _, _ = signed()
        copy = etree.fromstring(etree.tostring(root))
        for ref in copy.findall("cac:AdditionalDocumentReference", NS):
            if ref.findtext("cbc:ID", namespaces=NS) == "PIH":
                ref.find(".//cbc:EmbeddedDocumentBinaryObject", NS).text = "forged"
        with self.assertRaises(signing.SignatureInvalid):
            signing.verify_invoice(copy)

    def test_qr_and_signature_blocks_are_excluded_from_the_hash_but_everything_else_counts(self):
        inv = make()
        base = signing.invoice_hash_b64(ubl.build_xml(inv))
        root, res, _, _ = signed(inv)
        self.assertEqual(res["invoice_hash"], base)                                  # adding signature + QR does not move the hash
        self.assertNotEqual(signing.invoice_hash_b64(ubl.build_xml(make(icv=2))), base)
        self.assertNotEqual(signing.invoice_hash_b64(ubl.build_xml(make(lines=[ubl.Line("x", 1, 79901)]))), base)

    def test_a_forged_signature_from_another_key_fails(self):
        root, _, _, _ = signed()
        other = certs.generate_key()
        copy = etree.fromstring(etree.tostring(root))
        sv = copy.find(".//ds:SignatureValue", NS)
        si = copy.find(".//ds:SignedInfo", NS)
        sv.text = base64.b64encode(other.sign(signing._c14n(si), ec.ECDSA(hashes.SHA256()))).decode()
        with self.assertRaises(signing.SignatureInvalid):
            signing.verify_invoice(copy)

    def test_qr_embeds_hash_signature_and_key(self):
        root, res, key, cert = signed(simplified=True)
        qr = root.find(".//cac:AdditionalDocumentReference[cbc:ID='QR']//cbc:EmbeddedDocumentBinaryObject", NS).text
        p = tlv.parse_qr(qr)
        self.assertEqual(p[6].decode(), res["invoice_hash"])
        self.assertEqual(base64.b64encode(p[7]).decode(), res["signature"])
        self.assertIn(9, p)                                                          # simplified invoices also carry the certificate's signature
        self.assertEqual((p[4].decode(), p[5].decode()), ("918.85", "119.85"))
        signed_b2b = signed()[0].find(".//cac:AdditionalDocumentReference[cbc:ID='QR']//cbc:EmbeddedDocumentBinaryObject", NS).text
        self.assertNotIn(9, tlv.parse_qr(signed_b2b))

    def test_the_hash_chain_links_documents(self):
        key = certs.generate_key()
        pih, hashes_ = ubl.PIH_FIRST, []
        for icv in (1, 2, 3):
            root, res, _, _ = signed(make(icv=icv, pih=pih, uuid=str(uuid.uuid4())), key=key)
            self.assertEqual(root.xpath("string(//cac:AdditionalDocumentReference[cbc:ID='PIH']//cbc:EmbeddedDocumentBinaryObject)", namespaces=NS), pih)
            pih = res["invoice_hash"]
            hashes_.append(pih)
        self.assertEqual(len(set(hashes_)), 3)


class CsrTests(unittest.TestCase):
    def csr(self, env="sandbox", **kw):
        key = certs.generate_key()
        args = dict(env=env, common_name="omniflow-egs-1", org="شركة أومني فلو", org_unit="Riyadh", vat_number="399999999900003",
                    unit_serial="1-OmniFlow|2-Billing|3-" + str(uuid.uuid4()), address="الرياض 12222", business_category="Software")
        args.update(kw)
        return key, certs.build_csr(key, **args)

    def test_csr_has_zatca_subject_template_and_san(self):
        key, pem = self.csr()
        csr = x509.load_pem_x509_csr(pem)
        self.assertTrue(csr.is_signature_valid)
        self.assertIsInstance(csr.public_key(), ec.EllipticCurvePublicKey)
        self.assertEqual(csr.public_key().curve.name, "secp256k1")
        subject = {a.oid.dotted_string: a.value for a in csr.subject}
        self.assertEqual(subject["2.5.4.6"], "SA")
        self.assertEqual(subject["2.5.4.3"], "omniflow-egs-1")
        tmpl = csr.extensions.get_extension_for_oid(certs.OID_TEMPLATE).value.value
        self.assertIn(b"TSTZATCA-Code-Signing", tmpl)
        san = csr.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.DirectoryName)[0]
        sd = {a.oid.dotted_string: a.value for a in san}
        self.assertEqual((sd["0.9.2342.19200300.100.1.1"], sd["2.5.4.12"]), ("399999999900003", "1100"))
        self.assertTrue(sd["2.5.4.5"].startswith("1-OmniFlow|2-Billing|3-"))

    def test_environment_selects_the_template_and_bad_input_is_refused(self):
        for env, name in (("simulation", b"PREZATCA-Code-Signing"), ("production", b"ZATCA-Code-Signing")):
            _, pem = self.csr(env)
            self.assertIn(name, x509.load_pem_x509_csr(pem).extensions.get_extension_for_oid(certs.OID_TEMPLATE).value.value)
        with self.assertRaises(ValueError):
            self.csr(vat_number="12345")
        with self.assertRaises(ValueError):
            self.csr(vat_number="299999999900003")                                   # must start and end with 3
        with self.assertRaises(ValueError):
            self.csr("staging")

    def test_request_body_is_base64_of_the_pem(self):
        _, pem = self.csr()
        self.assertEqual(base64.b64decode(certs.csr_request_body(pem)), pem)

    def test_key_pem_round_trip(self):
        key = certs.generate_key()
        self.assertEqual(certs.key_from_pem(certs.key_to_pem(key)).private_numbers(), key.private_numbers())


class ClientTests(unittest.IsolatedAsyncioTestCase):
    def client(self, handler, env="sandbox"):
        return ZatcaClient(env=env, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    CREDS = Credentials(binary_security_token="dG9rZW4=", secret="s3cret")                        # pragma: allowlist secret

    def test_sandbox_is_the_default_and_production_is_double_locked(self):
        self.assertEqual(get_settings().einvoice_env, "sandbox")
        self.assertIn("developer-portal", base_url())
        with patch.object(get_settings(), "einvoice_env", "production"):
            with self.assertRaises(EInvoiceError):
                base_url("production")                                                       # env set but not explicitly allowed
            with patch.object(get_settings(), "einvoice_allow_production_submit", True):
                self.assertTrue(base_url("production").endswith("/core"))
        with patch.object(get_settings(), "einvoice_allow_production_submit", True):
            with self.assertRaises(EInvoiceError):
                base_url("production")                                                       # allowed but env still sandbox
        with self.assertRaises(EInvoiceError):
            ZatcaClient(env="production")

    async def test_clearance_and_reporting_use_the_right_endpoint_headers_and_basic_auth(self):
        seen = []

        def handler(req: httpx.Request):
            seen.append(req)
            return httpx.Response(200, json={"clearanceStatus": "CLEARED", "validationResults": {"status": "PASS"}})
        zc = self.client(handler)
        out = await zc.clear(self.CREDS, invoice_hash="h", uuid="u", signed_xml=b"<Invoice/>")
        await zc.report(self.CREDS, invoice_hash="h", uuid="u", signed_xml=b"<Invoice/>")
        self.assertEqual(out["clearanceStatus"], "CLEARED")
        clear, report = seen
        self.assertTrue(clear.url.path.endswith("/invoices/clearance/single") and report.url.path.endswith("/invoices/reporting/single"))
        self.assertEqual((clear.headers["Clearance-Status"], report.headers["Clearance-Status"]), ("1", "0"))
        self.assertEqual(clear.headers["Accept-Version"], "V2")
        self.assertEqual(base64.b64decode(clear.headers["Authorization"].split()[1]), b"dG9rZW4=:s3cret")
        body = json.loads(clear.content)
        self.assertEqual((body["invoiceHash"], body["uuid"], base64.b64decode(body["invoice"])), ("h", "u", b"<Invoice/>"))

    async def test_onboarding_endpoints(self):
        seen = []

        def handler(req):
            seen.append(req)
            return httpx.Response(200, json={"requestID": "1234", "binarySecurityToken": "dG9r", "secret": "x"})
        zc = self.client(handler)
        await zc.request_compliance_csid("Y3Ny", "123456")
        await zc.compliance_check(self.CREDS, invoice_hash="h", uuid="u", signed_xml=b"x")
        await zc.request_production_csid(self.CREDS, "1234")
        self.assertEqual([r.url.path.rsplit("/e-invoicing/developer-portal", 1)[-1] for r in seen], ["/compliance", "/compliance/invoices", "/production/csids"])
        self.assertEqual(seen[0].headers["OTP"], "123456")
        self.assertNotIn("Authorization", seen[0].headers)
        self.assertEqual(json.loads(seen[2].content), {"compliance_request_id": "1234"})

    async def test_status_codes_map_to_retryable_or_final_errors(self):
        cases = {400: (False, 400), 401: (False, 401), 409: (False, 409), 429: (True, 429), 500: (True, 500), 503: (True, 503)}
        for code, (retryable, status) in cases.items():
            zc = self.client(lambda req, c=code: httpx.Response(c, json={"validationResults": {"errorMessages": [{"code": "X"}]}}))
            with self.subTest(code), self.assertRaises(EInvoiceError) as cm:
                await zc.report(self.CREDS, invoice_hash="h", uuid="u", signed_xml=b"x")
            self.assertEqual((cm.exception.retryable, cm.exception.status), (retryable, status))
        ok = await self.client(lambda req: httpx.Response(202, json={"reportingStatus": "REPORTED", "validationResults": {"warningMessages": [{"code": "W"}]}})) \
            .report(self.CREDS, invoice_hash="h", uuid="u", signed_xml=b"x")
        self.assertEqual((ok["http_status"], ok["reportingStatus"]), (202, "REPORTED"))                    # warnings are accepted

    async def test_network_failure_is_retryable(self):
        def boom(req):
            raise httpx.ConnectError("down")
        with self.assertRaises(EInvoiceError) as cm:
            await self.client(boom).report(self.CREDS, invoice_hash="h", uuid="u", signed_xml=b"x")
        self.assertTrue(cm.exception.retryable)


if __name__ == "__main__":
    unittest.main()
