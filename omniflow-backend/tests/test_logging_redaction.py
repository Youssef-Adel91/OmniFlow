"""PII / secret redaction in structured logs."""
import json
import unittest

from src.shared.observability import logging_config as lc


def run(**kw):
    return lc.redact_processor(None, "info", {"event": "x", **kw})


class RedactionTests(unittest.TestCase):
    def test_secret_keys_and_body_keys_are_removed(self):
        out = run(access_token="EAAGabcdefghijklmnopqrstuvwxyz0123456789", authorization="Bearer abc.def.ghi", text="أريد شقة في الرياض", client_secret="s", ok="fine")
        self.assertEqual(out["access_token"], "[redacted]")
        self.assertEqual(out["authorization"], "[redacted]")
        self.assertEqual(out["client_secret"], "[redacted]")
        self.assertRegex(out["text"], r"^\[redacted:\d+ chars\]$")
        self.assertEqual(out["ok"], "fine")

    def test_phone_and_email_are_masked_by_key_and_inside_free_text(self):
        out = run(customer_phone="+966501234567", email="sara@example.com", note="اتصل على +966 50 123 4567 أو sara@example.com", n=5, flag=True)
        self.assertEqual(out["customer_phone"], "***67")
        self.assertEqual(out["email"], "s***@example.com")
        self.assertNotIn("501234567", out["note"])
        self.assertNotIn("sara@", out["note"])
        self.assertEqual((out["n"], out["flag"]), (5, True))

    def test_key_shapes_in_free_text_and_nested_values(self):
        out = run(error="401 for Bearer abcdefghijklmnop and sk-abcdefghijklmnopqrstuv", payload={"phone": "0501234567", "inner": {"password": "x", "list": ["a@b.co"]}}, ids=["u1"] * 100)
        self.assertNotIn("abcdefghijklmnop", out["error"])
        self.assertNotIn("sk-", out["error"])
        self.assertEqual(out["payload"]["phone"], "***67")
        self.assertEqual(out["payload"]["inner"]["password"], "[redacted]")
        self.assertEqual(len(out["ids"]), 50)
        json.dumps(out)                                             # always serialisable

    def test_event_level_timestamp_pass_through_and_ids_are_not_mangled(self):
        out = lc.redact_processor(None, "info", {"event": "customer_created", "level": "info", "timestamp": "2026-10-10T10:00:00Z",
                                                 "tenant_id": "3f2b8c1e-5a47-4c0b-9e0a-1c2d3e4f5a6b", "amount_halalas": 34385})
        self.assertEqual((out["event"], out["tenant_id"], out["amount_halalas"]), ("customer_created", "3f2b8c1e-5a47-4c0b-9e0a-1c2d3e4f5a6b", 34385))

    def test_digit_only_uuids_in_id_fields_survive(self):
        u = "12345678-1234-1234-1234-123456789012"
        self.assertEqual(run(tenant_id=u, event_id=u)["tenant_id"], u)

    def test_long_strings_are_truncated(self):
        self.assertLessEqual(len(run(blob="x" * 10_000)["blob"]), 2001)


if __name__ == "__main__":
    unittest.main()
