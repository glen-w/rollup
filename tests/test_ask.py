"""Grounded Ask over FTS hits."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rollup.ask import REFUSE_MESSAGE, answer_question, parse_ask_answer
from rollup.cli import build_parser
from rollup.corpus import CorpusHit, fts5_available, upsert_corpus_row
from rollup.corpus_cmd import cmd_ask
from rollup.state import init_db

pytestmark = pytest.mark.skipif(
    not fts5_available(),
    reason="SQLite FTS5 is not available in this build",
)


def _hit(key: str, title: str, snippet: str) -> CorpusHit:
    return CorpusHit(
        message_key=key,
        title=title,
        sender="a@example.com",
        folder="tech",
        snippet=snippet,
        summary="",
        rank=1.0,
    )


def test_ask_empty_hits_refuses() -> None:
    result = answer_question("What shipped?", (), complete=lambda p: "nope")
    assert result.refused
    assert result.answer == REFUSE_MESSAGE
    assert result.llm_used is False


def test_ask_mocked_complete_keeps_citations() -> None:
    hits = (
        _hit("mid:a@x", "Local-first evals", "The harness shipped last week"),
        _hit("mid:b@x", "Other", "Unrelated"),
    )

    def complete(prompt: str) -> str:
        assert "mid:a@x" in prompt
        return json.dumps(
            {
                "answer": "The harness shipped last week.",
                "citations": ["mid:a@x", "mid:forged@x"],
            }
        )

    result = answer_question("What shipped?", hits, complete=complete)
    assert not result.refused
    assert result.llm_used
    assert result.citations == ("mid:a@x",)
    assert "harness" in result.answer.lower()


def test_parse_ask_answer_rejects_junk() -> None:
    assert parse_ask_answer("not json", {"mid:a@x"}) is None
    assert parse_ask_answer("{}", {"mid:a@x"}) is None


def test_ask_without_llm_lists_excerpts() -> None:
    hits = (_hit("mid:a@x", "Title", "Body"),)
    result = answer_question("What?", hits, complete=None)
    assert not result.refused
    assert result.llm_used is False
    assert "Ask requires --ollama" in result.answer


def test_ask_cli_without_ollama(tmp_path: Path, capsys) -> None:
    state = tmp_path / "state"
    state.mkdir()
    conn = init_db(state / "rollup.db")
    upsert_corpus_row(
        conn,
        message_key="mid:ask@x",
        title="Helium briefing",
        body_text="Helium notes for the week",
    )
    conn.commit()
    conn.close()
    args = build_parser().parse_args(
        ["ask", "helium", "--state-dir", str(state), "--json"]
    )
    assert cmd_ask(args) == 2
    data = json.loads(capsys.readouterr().out)
    assert data["llm_used"] is False
    assert data["hits"]


def test_ask_cli_refuses_empty(tmp_path: Path, capsys) -> None:
    state = tmp_path / "state"
    state.mkdir()
    init_db(state / "rollup.db").close()
    args = build_parser().parse_args(
        ["ask", "zzzz-no-hits", "--state-dir", str(state), "--json"]
    )
    assert cmd_ask(args) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["refused"] is True
    assert data["answer"] == REFUSE_MESSAGE
