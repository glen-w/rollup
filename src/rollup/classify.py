"""Heuristic newsletter type classification."""

from __future__ import annotations

import re

from rollup.models import ClassifiedMessage, NewsletterType, ParsedMessage

# Tunable thresholds
LINK_ROUNDUP_MIN_LINKS = 8
LINK_ROUNDUP_MIN_RATIO = 0.02
SHORT_UPDATE_MAX_WORDS = 400
SHORT_UPDATE_MAX_LINKS = 5
ESSAY_MIN_WORDS = 1200
ESSAY_MAX_LINKS = 8
MULTI_SECTION_MIN_HEADINGS = 3
MULTI_SECTION_MIN_BULLETS = 10
MULTI_SECTION_MIN_WORDS = 600

HEADING_LINE_RE = re.compile(r"^#{1,3}\s", re.MULTILINE)
BULLET_RE = re.compile(r"^[\-\*•]\s", re.MULTILINE)


def _word_count(text: str) -> int:
    return len(text.split())


def _bullet_count(text: str) -> int:
    return len(BULLET_RE.findall(text))


def _heading_count_text(text: str) -> int:
    return len(HEADING_LINE_RE.findall(text))


def classify_message(parsed: ParsedMessage) -> ClassifiedMessage:
    """Classify a parsed message using deterministic heuristics."""
    try:
        if not parsed.body_text.strip():
            scores = (("unclassified", 1.0),)
            return ClassifiedMessage(
                parsed=parsed,
                newsletter_type="unclassified",
                classification_scores=scores,
            )

        word_count = _word_count(parsed.body_text)
        link_count = max(len(parsed.links), parsed.html_link_count)
        heading_count = max(_heading_count_text(parsed.body_text), parsed.html_heading_count)
        bullet_count = _bullet_count(parsed.body_text)
        ratio = link_count / max(word_count, 1)

        scores: dict[str, float] = {
            "link_roundup": 0.0,
            "short_update": 0.0,
            "essay": 0.0,
            "multi_section_digest": 0.0,
        }

        if link_count >= LINK_ROUNDUP_MIN_LINKS and ratio > LINK_ROUNDUP_MIN_RATIO:
            scores["link_roundup"] = 0.9
        if heading_count >= MULTI_SECTION_MIN_HEADINGS or (
            bullet_count > MULTI_SECTION_MIN_BULLETS and word_count > MULTI_SECTION_MIN_WORDS
        ):
            scores["multi_section_digest"] = 0.85
        elif word_count < SHORT_UPDATE_MAX_WORDS and link_count < SHORT_UPDATE_MAX_LINKS:
            scores["short_update"] = 0.85
        if word_count > ESSAY_MIN_WORDS and link_count < ESSAY_MAX_LINKS and bullet_count < 5:
            scores["essay"] = 0.8

        best_type: NewsletterType = max(scores, key=lambda k: scores[k])  # type: ignore[arg-type]
        if scores[best_type] == 0.0:
            best_type = "multi_section_digest"
            scores["multi_section_digest"] = 0.5

        sorted_scores = tuple(sorted(scores.items(), key=lambda x: -x[1]))
        return ClassifiedMessage(
            parsed=parsed,
            newsletter_type=best_type,
            classification_scores=sorted_scores,
        )
    except Exception:
        return ClassifiedMessage(
            parsed=parsed,
            newsletter_type="unclassified",
            classification_scores=(("unclassified", 1.0),),
        )
