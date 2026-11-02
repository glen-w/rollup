"""Tokenize interest prose for lexical overlap."""

from __future__ import annotations

import re

_TOKEN = re.compile(r"[a-z0-9]{3,}")
_STOP = frozenset(
    {
        "the",
        "and",
        "for",
        "that",
        "this",
        "with",
        "from",
        "are",
        "was",
        "were",
        "have",
        "has",
        "not",
        "but",
        "you",
        "your",
        "about",
        "into",
        "than",
        "then",
        "them",
        "they",
        "their",
        "what",
        "when",
        "which",
        "while",
        "will",
        "would",
        "could",
        "should",
        "also",
        "just",
        "more",
        "some",
        "such",
        "only",
        "over",
        "after",
        "before",
        "week",
        "newsletter",
    }
)


def tokenize(text: str) -> frozenset[str]:
    if not text or not text.strip():
        return frozenset()
    return frozenset(
        tok for tok in _TOKEN.findall(text.lower()) if tok not in _STOP
    )


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    if union == 0:
        return 0.0
    return inter / union
