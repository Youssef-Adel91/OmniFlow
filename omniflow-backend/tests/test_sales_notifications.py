"""Every notification kind the sales code sends must be registered — an unknown kind is swallowed by notify_safe, so staff would silently never be told."""
import re
import unittest
from pathlib import Path

from src.shared.services.notifications import KINDS

SRC = Path(__file__).resolve().parents[1] / "src" / "sales"


class SalesNotificationKindsTests(unittest.TestCase):
    def test_all_kinds_used_by_the_sales_package_are_registered(self):
        used = set()
        for f in SRC.glob("*.py"):
            used |= set(re.findall(r'notify(?:_safe)?\([^"\n]*"([a-z_]+)"', f.read_text(encoding="utf-8")))
        self.assertTrue({"hot_lead", "viewing_booked", "viewing_reminder"} <= used, used)
        self.assertEqual(sorted(k for k in used if k not in KINDS), [])


if __name__ == "__main__":
    unittest.main()
