"""Corpus search (GET) and grounded Ask (POST)."""

from __future__ import annotations

from flask import (
    Blueprint,
    current_app,
    g,
    redirect,
    render_template,
    request,
    url_for,
)

from rollup.ask import ASK_TOP_K, REFUSE_MESSAGE, answer_question
from rollup.config import DEFAULT_LLM_PROVIDER, DEFAULT_OLLAMA_MODEL, DEFAULT_OLLAMA_URL
from rollup.corpus import SEARCH_LIMIT_DEFAULT, SEARCH_LIMIT_MAX, CorpusHit, search_corpus
from rollup.web.config import load_web_config_document
from rollup.web.csrf import ensure_csrf_token, rotate_csrf_token, validate_csrf_token
from rollup.web_ids import IdError, encode_opaque

bp = Blueprint("search", __name__)
MAX_QUESTION_LEN = 500


def _ask_enabled() -> bool:
    try:
        from rollup.config_service import resolve_effective

        doc = load_web_config_document()
        return resolve_effective(doc.loaded).ollama_contacted
    except Exception:
        return False


def _web_complete():
    if not _ask_enabled():
        return None
    from rollup.config_service import resolve_effective
    from rollup.llm_client import CompletionRequest, get_llm_client

    doc = load_web_config_document()
    sticky = resolve_effective(doc.loaded).sticky
    provider = sticky.get("llm_provider") or DEFAULT_LLM_PROVIDER
    model = (
        sticky.get("llm_model")
        or sticky.get("ollama_model")
        or DEFAULT_OLLAMA_MODEL
    )
    ollama_url = sticky.get("ollama_url") or DEFAULT_OLLAMA_URL
    allow_remote = bool(sticky.get("allow_remote_ollama"))

    def complete(prompt: str) -> str:
        client = get_llm_client(
            provider,
            ollama_url=ollama_url,
            allow_remote=allow_remote,
            llm_api_base=sticky.get("llm_api_base"),
        )
        result = client.complete(
            CompletionRequest(
                model=model,
                prompt=prompt,
                stream=False,
                timeout_seconds=60.0,
                temperature=0.1,
                response_format_json=True,
            ),
            max_output_chars=4_000,
            show_progress=False,
        )
        return result.text

    return complete


def _hits_for_template(hits):
    rows = []
    for hit in hits:
        try:
            body_url = url_for(
                "messages.message_body", id_enc=encode_opaque(hit.message_key)
            )
        except (IdError, Exception):
            body_url = None
        rows.append(
            {
                "message_key": hit.message_key,
                "title": hit.title or hit.message_key,
                "sender": hit.sender,
                "folder": hit.folder,
                "snippet": hit.snippet,
                "summary": hit.summary,
                "body_url": body_url,
            }
        )
    return rows


@bp.get("/search")
def search_index():
    ensure_csrf_token()
    query = (request.args.get("q") or "").strip()
    hits: tuple[CorpusHit, ...] = ()
    if query:
        try:
            limit = min(
                SEARCH_LIMIT_MAX,
                max(1, int(request.args.get("limit", SEARCH_LIMIT_DEFAULT))),
            )
        except ValueError:
            limit = SEARCH_LIMIT_DEFAULT
        hits = search_corpus(g.db, query, limit=limit)
    return render_template(
        "search/index.html",
        query=query,
        hits=_hits_for_template(hits),
        ask_enabled=_ask_enabled(),
        ask_result=None,
        refuse_message=REFUSE_MESSAGE,
        bind_host=current_app.config.get("WEB_BIND_HOST", "127.0.0.1"),
    )


@bp.post("/ask")
def ask_post():
    if not validate_csrf_token(request.form.get("csrf_token")):
        return render_template("errors/400.html", message="CSRF validation failed"), 400
    rotate_csrf_token()
    question = (request.form.get("q") or "").strip()[:MAX_QUESTION_LEN]
    if not question:
        return redirect(url_for("search.search_index"))
    hits = search_corpus(g.db, question, limit=ASK_TOP_K)
    complete = _web_complete() if _ask_enabled() else None
    result = answer_question(question, hits, complete=complete)
    return render_template(
        "search/index.html",
        query=question,
        hits=_hits_for_template(result.hits),
        ask_enabled=_ask_enabled(),
        ask_result=result,
        refuse_message=REFUSE_MESSAGE,
        bind_host=current_app.config.get("WEB_BIND_HOST", "127.0.0.1"),
    )
