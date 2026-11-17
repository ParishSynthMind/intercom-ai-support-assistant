"""Safety around customer text.

- Customer messages are DATA. They go to Claude inside a fenced block with a
  plain instruction never to follow orders found inside it, and obvious
  injection attempts are detected so the draft is held and flagged.
- Opt-out words ("stop", "unsubscribe", "don't use AI") put the contact on the
  suppression list: no AI draft, a note tells the team why.
- Logs never carry personal data: emails, phone numbers and card-like numbers
  are masked before anything is written.
"""

from __future__ import annotations

import html
import re

INJECTION_PATTERNS = [
    r"ignore (all |any |the )?(previous|prior|above|earlier) (instructions|rules|prompts?)",
    r"disregard (all |any |the )?(previous|prior|above|earlier|your) (instructions|rules)",
    r"forget (all |your )?(previous |prior )?(instructions|rules)",
    r"you are now\b",
    r"new instructions?:",
    r"system prompt",
    r"reveal (your|the) (prompt|instructions|system|api key|token)",
    r"\bact as (an? )?(admin|administrator|developer|system)",
    r"(send|give|show|print) (me )?(the |your )?(api key|access token|password|secret)",
    r"refund (me )?(immediately|now) without",
    r"approve (this|the) (refund|request) automatically",
    r"<\s*/?\s*(system|assistant|instructions?)\s*>",
    r"```\s*system",
]
_INJECTION_RE = re.compile("|".join(INJECTION_PATTERNS), re.IGNORECASE)

OPT_OUT_RE = re.compile(
    r"^\s*(stop|unsubscribe|opt[ -]?out)\s*[.!]*\s*$"
    r"|\b(no|don'?t|do not|stop) (use |using )?(ai|a\.i\.|bots?|chatbots?)\b"
    r"|\bspeak to a (real )?(human|person) only\b",
    re.IGNORECASE,
)

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
CARD_RE = re.compile(r"\b(?:\d[ -]?){13,19}\b")
PHONE_RE = re.compile(r"(?<![\w])\+?\d[\d ()-]{7,}\d(?![\w])")


def looks_like_injection(text: str) -> bool:
    return bool(_INJECTION_RE.search(text or ""))


def is_opt_out(text: str) -> bool:
    return bool(OPT_OUT_RE.search(text or ""))


def redact(text: str) -> str:
    """Mask personal data for logs. Order matters: cards before phones."""
    if not text:
        return ""
    text = EMAIL_RE.sub("[email]", text)
    text = CARD_RE.sub("[number]", text)
    text = PHONE_RE.sub("[phone]", text)
    return text


def strip_html(text: str) -> str:
    text = re.sub(r"<br\s*/?>|</p>\s*<p>", "\n", text or "", flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip()


def fence(text: str, limit: int) -> str:
    """Wrap customer text for the prompt: trimmed, size-capped, fence markers neutralised."""
    text = (text or "").replace("<<<", "‹‹‹").replace(">>>", "›››")
    if len(text) > limit:
        text = text[:limit] + "\n[...trimmed]"
    return f"<<<CUSTOMER_MESSAGE\n{text}\nCUSTOMER_MESSAGE>>>"


def to_html(text: str) -> str:
    """Plain text to the small HTML Intercom notes and replies accept, escaped."""
    paras = [p.strip() for p in (text or "").split("\n\n") if p.strip()]
    return "".join("<p>" + html.escape(p, quote=False).replace("\n", "<br>") + "</p>" for p in paras)
