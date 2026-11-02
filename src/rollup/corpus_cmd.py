"""CLI for corpus search and grounded Ask."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from rollup.ask import ASK_TOP_K, REFUSE_MESSAGE, answer_question, make_complete
from rollup.corpus import (
    SEARCH_LIMIT_DEFAULT,
    SEARCH_LIMIT_MAX,
    CorpusHit,
    search_corpus,
)
from rollup.state import connect_db_readonly, get_schema_version


def _open_ro(state_dir: Path):
    db = Path(state_dir).expanduser() / "rollup.db"
    if not db.is_file():
        print(f"ERROR: no state database at {db}", file=sys.stderr)
        return None
    return connect_db_readonly(db)


def _hits_to_dicts(hits: tuple[CorpusHit, ...]) -> list[dict]:
    return [
        {
            "message_key": h.message_key,
            "title": h.title,
            "sender": h.sender,
            "folder": h.folder,
            "snippet": h.snippet,
            "summary": h.summary,
            "rank": h.rank,
        }
        for h in hits
    ]


def cmd_search(args: argparse.Namespace) -> int:
    conn = _open_ro(Path(args.state_dir))
    if conn is None:
        return 1
    try:
        ver = get_schema_version(conn)
        if ver < 17:
            print(
                "ERROR: corpus search needs schema v17; run rollup doctor or a digest",
                file=sys.stderr,
            )
            return 1
        query = " ".join(args.query).strip()
        if not query:
            print("ERROR: empty search query", file=sys.stderr)
            return 2
        limit = max(1, min(int(args.limit), SEARCH_LIMIT_MAX))
        hits = search_corpus(conn, query, limit=limit)
        if args.json:
            print(json.dumps({"query": query, "hits": _hits_to_dicts(hits)}))
        elif not hits:
            print("No matching items in the Rollup cache.")
        else:
            for hit in hits:
                title = hit.title or hit.message_key
                print(f"{title}")
                print(f"  {hit.message_key}")
                if hit.folder:
                    print(f"  {hit.folder}")
                if hit.snippet:
                    print(f"  {hit.snippet}")
    finally:
        conn.close()
    return 0


def cmd_ask(args: argparse.Namespace) -> int:
    conn = _open_ro(Path(args.state_dir))
    if conn is None:
        return 1
    try:
        ver = get_schema_version(conn)
        if ver < 17:
            print(
                "ERROR: Ask needs schema v17; run rollup doctor or a digest",
                file=sys.stderr,
            )
            return 1
        question = " ".join(args.question).strip()
        if not question:
            print("ERROR: empty question", file=sys.stderr)
            return 2
        hits = search_corpus(conn, question, limit=ASK_TOP_K)
        from rollup.cli import _build_config

        config = _build_config(args)
        complete = make_complete(config)
        result = answer_question(question, hits, complete=complete)
        if args.json:
            print(
                json.dumps(
                    {
                        "question": question,
                        "answer": result.answer,
                        "citations": list(result.citations),
                        "refused": result.refused,
                        "llm_used": result.llm_used,
                        "hits": _hits_to_dicts(result.hits),
                    }
                )
            )
        else:
            print(result.answer)
            if result.citations:
                print("")
                print("Citations:")
                for key in result.citations:
                    print(f"  {key}")
            elif result.hits and not result.refused:
                print("")
                print("Matching excerpts:")
                for hit in result.hits:
                    print(f"  {hit.message_key}  {hit.title}")
        if result.refused:
            return 0
        if complete is None:
            return 2
        return 0
    finally:
        conn.close()


def add_search_ask_subparsers(sub) -> None:
    from rollup.cli_parser import _add_common_args
    from rollup.config import DEFAULT_LLM_PROVIDER, DEFAULT_OLLAMA_URL

    search = sub.add_parser(
        "search",
        help="Search the local reader-body / title / summary corpus (FTS, no LLM)",
    )
    _add_common_args(search)
    search.add_argument("query", nargs="+", help="Search terms")
    search.add_argument(
        "--limit",
        type=int,
        default=SEARCH_LIMIT_DEFAULT,
        help=f"Max hits (default {SEARCH_LIMIT_DEFAULT}, max {SEARCH_LIMIT_MAX})",
    )
    search.add_argument("--json", action="store_true")

    ask = sub.add_parser(
        "ask",
        help="Ask a question grounded in corpus FTS hits (requires --ollama)",
    )
    _add_common_args(ask)
    ask.add_argument("question", nargs="+", help="Question to answer from the cache")
    ask.add_argument("--json", action="store_true")
    ollama_group = ask.add_mutually_exclusive_group()
    ollama_group.add_argument(
        "--ollama",
        action="store_true",
        help="Enable LLM answer (explicit opt-in; local Ollama by default)",
    )
    ollama_group.add_argument("--no-ollama", action="store_true")
    ask.add_argument("--ollama-url", default=DEFAULT_OLLAMA_URL)
    ask.add_argument("--ollama-model", default=None)
    ask.add_argument("--allow-remote-ollama", action="store_true", default=False)
    ask.add_argument(
        "--llm-provider",
        choices=["ollama", "litellm"],
        default=DEFAULT_LLM_PROVIDER,
    )
    ask.add_argument("--llm-model", default=None)
    ask.add_argument("--llm-api-base", default=None)
