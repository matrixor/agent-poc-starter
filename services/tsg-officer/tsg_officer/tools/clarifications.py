from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, Tuple


# ---------------------------------------------------------------------------
# Clarification / explanation handling
# ---------------------------------------------------------------------------

# Allow the user to request clarification up to 3 times for the same question.
# On the 4th request, the workflow will bypass the question and move on.
MAX_EXPLANATION_REQUESTS_PER_QUESTION: int = 3

# Marker value stored in followup/intake answers when a question is bypassed.
# We keep it short and stable for audit logs + downstream processing.
BYPASSED_ANSWER_VALUE: str = "[BYPASSED]"


# Common ways users request clarification.
#
# NOTE: We see a lot of “smart quotes” in browser input (e.g., “don’t” using U+2019).
# We normalize those characters in `looks_like_clarification_request`.
_CLARIFY_RE = re.compile(
    r"("  # group
    # English
    r"\b(i\s*(do\s*not|don'?t|dont)\s*(understand|get|know))\b|"
    r"\bnot\s+sure\b|"
    r"\bnot\s+familiar\b|"
    r"\b(can|could|would)\s+you\s+(explain|clarify|define)\b|"
    r"\bplease\s+(explain|clarify|define)\b|"
    r"\bwhat\s+(is|are|does)\b|"
    r"\bwhat\s+do\s+you\s+mean\b|"
    r"\bmeaning\s+of\b|"
    r"\bhelp\s+me\s+understand\b|"
    r"\bexample\b|"
    r"\btemplate\b|"
    # Chinese (common in Chubb global teams)
    r"不(懂|明白)|我不(懂|明白)|不太(懂|明白)|看不懂|什么意思|解释(一下)?|请(解释|说明)|能不能(解释|说明)|怎么(理解|定义)"
    r")",
    re.IGNORECASE,
)

_QUESTION_WORD_RE = re.compile(r"^(what|why|how|can|could|would|where|when|who)\b", re.IGNORECASE)


_SMART_PUNCT_TRANSLATION = str.maketrans(
    {
        # Apostrophes / single quotes
        "’": "'",
        "‘": "'",
        "‛": "'",
        "＇": "'",
        # Double quotes
        "“": '"',
        "”": '"',
    }
)


def _normalize_user_text(text: str) -> str:
    """Normalize user input for robust regex matching.

    Streamlit/browser inputs often contain smart punctuation (e.g. “don’t”).
    We normalize to NFKC and translate common quote characters to ASCII.
    """

    t = unicodedata.normalize("NFKC", text or "")
    return t.translate(_SMART_PUNCT_TRANSLATION)


def looks_like_clarification_request(text: str) -> bool:
    """Best-effort detection of a user asking for an explanation.

    We intentionally keep this heuristic simple and conservative.
    """

    t = _normalize_user_text(text).strip()
    if not t:
        return False

    # Normalize leading punctuation/quotes for question detection.
    t2 = t.lstrip(" \t\n\r\"'")

    # If the user reply is itself a question, it's likely a clarification request.
    #
    # We treat *short* questions as clarification requests even if they don't start
    # with a question word (e.g., "Hallucinations?" or "Enterprise objectives?").
    if "?" in t2:
        if _QUESTION_WORD_RE.search(t2) or len(t2.split()) <= 8:
            return True

    # Users frequently omit the trailing '?' in chat.
    # If the message *starts* like a question (how/what/why/can/...) and is short,
    # treat it as a clarification request.
    if _QUESTION_WORD_RE.search(t2):
        # Avoid misclassifying heading-style answers like "How we do X ...".
        if not t2.lower().startswith(("how we ", "how our ")):
            if len(t2.split()) <= 12 and ":" not in t2 and "\n" not in t2:
                return True

    return bool(_CLARIFY_RE.search(t2))


def bump_counter(counts: Dict[str, Any] | None, key: str) -> Tuple[Dict[str, int], int]:
    """Increment a per-question counter.

    Returns (new_counts, new_count).
    """

    base: Dict[str, int] = {}
    if isinstance(counts, dict):
        for k, v in counts.items():
            try:
                base[str(k)] = int(v)  # type: ignore[arg-type]
            except Exception:
                continue

    k2 = str(key or "").strip()
    if not k2:
        k2 = "unknown"

    n = int(base.get(k2, 0) or 0) + 1
    base[k2] = n
    return base, n
