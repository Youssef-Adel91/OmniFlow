"""
ai_workers/vcard_gatekeeper/vcard_builder.py — Real VCard 3.0 file generation.

Builds the actual .vcf contact card the customer is asked to save (SRS §5.5
"Digital Contact VCard Gatekeeper" — the sequence diagram there shows a real
"Send VCard attachment" step, distinct from the instructional text). Before
this module existed, the `vobject` dependency (added for exactly this) was
never imported anywhere, and the gatekeeper only sent plain text.
"""
from __future__ import annotations

import vobject


def build_tenant_vcard(*, business_name: str, phone: str | None) -> bytes:
    """Render a VCard 3.0 file for the tenant's own WhatsApp Business number.

    `phone` should be the tenant's real, dialable WhatsApp number
    (Tenant.whatsapp_display_phone_number) — NOT the opaque Meta
    `whatsapp_phone_number_id`, which is not a phone number and would save a
    contact the customer can never actually call or message from their own
    phone's contact list.
    """
    card = vobject.vCard()
    card.add("n")
    card.n.value = vobject.vcard.Name(family=business_name, given="")
    card.add("fn")
    card.fn.value = business_name
    card.add("org")
    card.org.value = [business_name]
    if phone:
        tel = card.add("tel")
        tel.value = phone
        tel.type_param = "CELL"
    return card.serialize().encode("utf-8")


def _demo() -> None:
    """ponytail self-check: output is a well-formed, parseable VCard 3.0 file."""
    vcf = build_tenant_vcard(business_name="عقارات الاختبار", phone="+966500000000")
    assert vcf.startswith(b"BEGIN:VCARD"), "not a VCard file"
    assert b"VERSION:3.0" in vcf, "wrong VCard version"
    assert b"+966500000000" in vcf, "phone number missing from output"

    # Round-trip through vobject's own parser to prove it's not just
    # well-formed by coincidence — a real WhatsApp client will parse this
    # the same way.
    parsed = vobject.readOne(vcf.decode("utf-8"))
    assert parsed.fn.value == "عقارات الاختبار"
    assert parsed.tel.value == "+966500000000"

    no_phone_vcf = build_tenant_vcard(business_name="No Phone Co", phone=None)
    assert b"TEL" not in no_phone_vcf, "should omit TEL entirely when no phone is on file"

    print(f"vcard_builder self-check passed: {len(vcf)} bytes, round-trips through vobject's parser")


if __name__ == "__main__":
    _demo()
