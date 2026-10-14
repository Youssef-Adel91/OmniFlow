"""
Pure text rules for Facebook/Instagram comments (no I/O, no Redis, no Graph calls):

  * classify_text      -> should this comment be ignored / hidden / answered?   (before any reply is queued)
  * violates_public_policy -> may this AI answer be posted PUBLICLY?             (prices and promises stay out of public view)
  * render_public_template -> the fixed public reply used in "public_and_private" mode and as the safe replacement.

Everything is conservative: when in doubt a comment is answered the normal way, EXCEPT public answers that quote a price or
promise a return, which are never posted publicly. The word lists are deliberately short and obvious; tenants add their own
(`ignore_keywords`, `hide_keywords`) in Settings.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from src.shared.services.bulk_import.text import norm_text

# Built-in insults / scam accusations. Matched on the normalised text (no diacritics, unified letters) as whole words.
_ABUSIVE_WORDS = {
    "حقير", "حقيره", "زباله", "غبي", "غبيه", "حمار", "كلب", "كلاب", "وسخ", "قذر", "تافه", "ملعون",
    "نصاب", "نصابين", "نصابه", "حرامي", "حراميه", "احتيال", "محتال", "محتالين", "سرقه",
    "idiot", "stupid", "scam", "scammer", "scammers", "fraud", "thief", "trash", "garbage", "fuck", "shit", "bitch",
}
_URL = re.compile(r"(https?://|www\.|\b\w+\.(?:com|net|org|ly|me|io|xyz|ru|cn)\b)", re.I)
_LONG_REPEAT = re.compile(r"(.)\1{9,}")

# Prices / money amounts -- anything that looks like a quote. Checked on the raw text so "850,000" and "٨٥٠٫٠٠٠" are both caught.
_CURRENCY = r"(?:ريال|ر\.?\s?س|sar|sr|ر\.?\s?ق|درهم|aed|دينار|kwd|جنيه|egp|دولار|usd|\$|€|ألف|الف|مليون|مليار|k|m)"
_PRICE_AFTER = re.compile(rf"[\d٠-٩][\d٠-٩,.٬٫]*\s*{_CURRENCY}\b", re.I)
_PRICE_BEFORE = re.compile(rf"(?<!\w){_CURRENCY}\s*[\d٠-٩][\d٠-٩,.٬٫]*", re.I)
_BIG_NUMBER = re.compile(r"(?<![\d٠-٩])[\d٠-٩]{1,3}(?:[,٬][\d٠-٩]{3})+(?![\d٠-٩])|(?<![\d٠-٩])[\d٠-٩]{5,}(?![\d٠-٩])")
_PRICE_WORD_WITH_NUMBER = re.compile(r"(?:سعر|السعر|بسعر|يبدأ من|تبدأ من|price|starting from)[^.!؟?\n]{0,25}[\d٠-٩]", re.I)
_PROMISE = re.compile(r"(نضمن|نضمن لك|مضمون|ضمان\s*(?:عائد|ربح|استثمار)|عائد\s*(?:مضمون|سنوي)|ربح\s*(?:مضمون|اكيد)|بدون\s*مخاطر|guarantee[ds]?|risk[- ]free|"
                      r"عقد\s*ملزم|نتعهد|نلتزم\s*لك|خصم|تخفيض|تنزيل|discount|price\s*cut)", re.I)


# Posted publicly when the assistant's answer had to be withheld (price / promise) and NO private follow-up is sent: it invites the customer
# to message the page instead of claiming "we sent you the details".
PUBLIC_FALLBACK_NO_DM = "شكراً لتعليقك 🌟 راسلنا على الخاص وسنسعد بخدمتك بالتفاصيل."


@dataclass(frozen=True)
class TextVerdict:
    action: str              # "ignore" | "hide"
    reason: str


def _letters_only(text: str) -> str:
    """Drops emoji, symbols, punctuation and whitespace; what remains is real text (letters/digits)."""
    return "".join(ch for ch in unicodedata.normalize("NFKC", text or "") if unicodedata.category(ch)[0] in ("L", "N"))


def _words(norm: str) -> set[str]:
    return set(norm.split())


def _contains_keyword(norm_text_value: str, keywords: list[str]) -> bool:
    """Substring match on the normalised text (multi-word keywords and Arabic prefixes like "ال" work this way)."""
    for k in keywords:
        nk = norm_text(k)
        if nk and nk in norm_text_value:
            return True
    return False


def classify_text(text: str, *, ignore_keywords: list[str], hide_keywords: list[str], auto_hide_abusive: bool) -> TextVerdict | None:
    """None -> answer the comment normally. Otherwise the verdict and a short machine reason (logged, never shown to the commenter)."""
    raw = text or ""
    if len(_letters_only(raw)) < 2:
        return TextVerdict("ignore", "no_text")                         # emoji / punctuation only, or a single character
    norm = norm_text(raw)
    if auto_hide_abusive:
        if _words(norm) & _ABUSIVE_WORDS:
            return TextVerdict("hide", "abusive")
        if _URL.search(raw) or _LONG_REPEAT.search(raw):          # a phone number is NOT spam: it is a customer asking to be called
            return TextVerdict("hide", "spam")
        if _contains_keyword(norm, hide_keywords):
            return TextVerdict("hide", "hide_keyword")
    if _contains_keyword(norm, ignore_keywords):
        return TextVerdict("ignore", "ignore_keyword")
    return None


def violates_public_policy(text: str) -> str | None:
    """"price" / "promise" when an answer must NOT be posted publicly under a post; None when it is fine."""
    t = text or ""
    if _PRICE_AFTER.search(t) or _PRICE_BEFORE.search(t) or _PRICE_WORD_WITH_NUMBER.search(t) or _BIG_NUMBER.search(t):
        return "price"
    if _PROMISE.search(t):
        return "promise"
    return None


def render_public_template(template: str, *, name: str | None) -> str:
    """Fills {name}; a missing name drops the greeting's name cleanly (no "أهلاً  ")."""
    first = (name or "").strip().split(" ")[0][:30]
    out = template.replace("{name}", first)
    out = re.sub(r"\s{2,}", " ", out)
    return out.replace(" ،", "،").replace(" ,", ",").strip()
