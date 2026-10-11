"""Help-centre content guards: the new product articles exist, are findable, link only to real settings sections, and carry no hard-coded prices."""
import re
import unittest

from src.shared.services import support_kb

REQUIRED = {"getting-started", "plans-and-limits", "ai-quota-exhausted", "topup-packs", "vat-and-meta-fees", "invoices", "cancel-and-refund",
            "trial-ended", "invite-team", "meta-data-deletion", "whatsapp-via-meta",
            # the topics the launch brief names
            "import-properties", "connect-whatsapp", "connect-facebook-instagram", "ai-persona", "handoff-and-hours", "import-customers-consent", "pdpl-delete-customer"}
SETTINGS_SECTIONS = {"channels", "team", "hours", "ai", "notifications", "privacy", "billing", "developers", "identity"}
APP_PATHS = {"/dashboard", "/properties", "/customers", "/broadcasts", "/reports", "/knowledge", "/support"}


class HelpContentTests(unittest.TestCase):
    def test_required_articles_exist_and_ids_are_unique(self):
        ids = [a.id for a in support_kb.ARTICLES]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertFalse(REQUIRED - set(ids), REQUIRED - set(ids))

    def test_every_article_is_complete_and_findable_by_title_and_keyword(self):
        for a in support_kb.ARTICLES:
            self.assertTrue(a.title.strip() and len(a.answer) > 80, a.id)
            self.assertTrue(a.keywords, a.id)
            self.assertIn(a.id, [h["id"] for h in support_kb.search(a.title)], a.id)
            self.assertIn(a.id, [h["id"] for h in support_kb.search(a.keywords[0], limit=20)], f"{a.id} not found by its first keyword")

    def test_links_point_at_real_places(self):
        for a in support_kb.ARTICLES:
            self.assertEqual(bool(a.link), bool(a.link_label), a.id)
            if not a.link:
                continue
            path, _, frag = a.link.partition("#")
            self.assertTrue(path.startswith("/"), a.id)
            if path == "/settings":
                self.assertIn(frag, SETTINGS_SECTIONS, a.id)
            else:
                self.assertIn(path, APP_PATHS, a.id)

    def test_no_hardcoded_prices_that_would_drift_from_the_catalogue(self):
        for a in support_kb.ARTICLES:
            self.assertIsNone(re.search(r"\d[\d,.]*\s*(ر\.س|ريال|SAR)", a.answer), a.id)

    def test_articles_state_only_what_the_product_does(self):
        text = " ".join(a.answer for a in support_kb.ARTICLES)
        for banned in ("أول مساعد", "الأفضل", "مضمون 100", "بدون أي حدود"):
            self.assertNotIn(banned, text)


if __name__ == "__main__":
    unittest.main()
