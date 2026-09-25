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


def build_tenant_vcard(
    *,
    business_name: str,
    phone: str | None,
    category: str | None = None,
    note: str | None = None,
    email: str | None = None,
    website: str | None = None,
) -> bytes:
    """Render a VCard 3.0 file for the tenant's own WhatsApp Business number.

    `phone` should be the tenant's real, dialable WhatsApp number
    (Tenant.whatsapp_display_phone_number) — NOT the opaque Meta
    `whatsapp_phone_number_id`, which is not a phone number and would save a
    contact the customer can never actually call or message from their own
    phone's contact list.

    A real live test caught this: with only FN populated, the resulting
    contact card had a name and nothing else — no phone number defeats the
    entire point of the feature (letting the customer actually reach the
    business). `category`/`note`/`email`/`website` are all optional and
    sourced from the tenant's CompanyProfile when available, so the card
    carries whatever real business info onboarding actually collected,
    not just the name.
    """
    card = vobject.vCard()
    card.add("n")
    card.n.value = vobject.vcard.Name(family=business_name, given="")
    card.add("fn")
    card.fn.value = business_name
    org_value = f"{business_name} — {category}" if category else business_name
    card.add("org")
    card.org.value = [org_value]
    if phone:
        tel = card.add("tel")
        tel.value = phone
        tel.type_param = "CELL"
    if email:
        card.add("email")
        card.email.value = email
    if website:
        card.add("url")
        card.url.value = website
    if note:
        card.add("note")
        card.note.value = note[:500]
    return card.serialize().encode("utf-8")


def _demo() -> None:
    """ponytail self-check: output is a well-formed, parseable VCard 3.0 file."""
    vcf = build_tenant_vcard(
        business_name="عقارات الاختبار", phone="+966500000000",
        category="عقارات سكنية", note="نتعامل بمنتجات موثوقة وضمان حقيقي.",
        email="info@example.com", website="https://example.com",
    )
    assert vcf.startswith(b"BEGIN:VCARD"), "not a VCard file"
    assert b"VERSION:3.0" in vcf, "wrong VCard version"
    assert b"+966500000000" in vcf, "phone number missing from output"

    # Round-trip through vobject's own parser to prove it's not just
    # well-formed by coincidence — a real WhatsApp client will parse this
    # the same way.
    parsed = vobject.readOne(vcf.decode("utf-8"))
    assert parsed.fn.value == "عقارات الاختبار"
    assert parsed.tel.value == "+966500000000"
    assert "عقارات سكنية" in parsed.org.value[0]
    assert parsed.email.value == "info@example.com"
    assert parsed.url.value == "https://example.com"
    assert parsed.note.value == "نتعامل بمنتجات موثوقة وضمان حقيقي."

    no_phone_vcf = build_tenant_vcard(business_name="No Phone Co", phone=None)
    assert b"TEL" not in no_phone_vcf, "should omit TEL entirely when no phone is on file"

    print(f"vcard_builder self-check passed: {len(vcf)} bytes, round-trips through vobject's parser")


if __name__ == "__main__":
    _demo()
