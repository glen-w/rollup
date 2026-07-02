#!/usr/bin/env python3
"""Benchmark local Ollama-compatible models on fixed prompts."""

from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import time
from urllib import request

BUILTIN_PROMPTS = [
    "Summarize this newsletter blurb in concise bullets: A policy round-up covering AI regulation in the EU and US, plus two court decisions and one upcoming deadline.",
    "Summarize this product update newsletter in concise bullets: The company shipped a new API, deprecated an old SDK endpoint, and published migration guidance.",
    "Summarize this essay excerpt in concise bullets: The author argues that AI agents will reshape software operations, but progress depends on reliability, tooling, and human oversight.",
]


def _post_json(url: str, payload: dict[str, object], timeout: int) -> dict[str, object]:
    req = request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def benchmark_model(
    *,
    model: str,
    prompts: list[str],
    base_url: str,
    runs: int,
    num_ctx: int | None,
    timeout: int,
) -> dict[str, object]:
    records = []
    for run_index in range(runs):
        for prompt_index, prompt in enumerate(prompts):
            started = time.perf_counter()
            status = "ok"
            error = None
            output_text = ""
            try:
                payload = {
                    "model": model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"num_ctx": num_ctx} if num_ctx is not None else {},
                }
                data = _post_json(base_url, payload, timeout)
                output_text = str(data.get("response", "")).strip()
            except (
                Exception
            ) as exc:  # pragma: no cover - exercised through integration/mocks
                status = "error"
                error = str(exc)
            elapsed = time.perf_counter() - started
            records.append(
                {
                    "run": run_index + 1,
                    "prompt_index": prompt_index,
                    "status": status,
                    "error": error,
                    "input_chars": len(prompt),
                    "output_chars": len(output_text),
                    "elapsed_seconds": elapsed,
                    "chars_per_second": (len(output_text) / elapsed)
                    if elapsed and output_text
                    else 0.0,
                }
            )
    avg_elapsed = sum(record["elapsed_seconds"] for record in records) / max(
        len(records), 1
    )
    return {
        "model": model,
        "runs": runs,
        "prompt_count": len(prompts),
        "average_elapsed_seconds": avg_elapsed,
        "records": records,
    }


def _load_prompts(prompt_file: str | None) -> list[str]:
    if not prompt_file:
        return list(BUILTIN_PROMPTS)
    text = Path(prompt_file).read_text(encoding="utf-8")
    return [chunk.strip() for chunk in text.split("\n---\n") if chunk.strip()]


def _write_markdown(path: Path, results: list[dict[str, object]]) -> None:
    lines = [
        "# Ollama Benchmark Report",
        "",
        f"Generated: {datetime.now().astimezone().isoformat()}",
        "",
        "| Model | Avg seconds | Successful calls | Failed calls |",
        "|---|---:|---:|---:|",
    ]
    for result in results:
        records = list(result["records"])
        ok_count = sum(1 for record in records if record["status"] == "ok")
        err_count = sum(1 for record in records if record["status"] != "ok")
        lines.append(
            f"| {result['model']} | {result['average_elapsed_seconds']:.2f} | {ok_count} | {err_count} |"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Benchmark local Ollama-compatible models."
    )
    parser.add_argument("--models", required=True, help="Comma-separated model names.")
    parser.add_argument(
        "--prompt-file", help="Optional prompt file split by '\\n---\\n'."
    )
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--num-ctx", type=int)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--base-url", default="http://localhost:11434/api/generate")
    parser.add_argument("--out", required=True, help="Write JSON report here.")
    parser.add_argument("--markdown-out", help="Optional Markdown report path.")
    args = parser.parse_args(argv)

    prompts = _load_prompts(args.prompt_file)
    models = [model.strip() for model in args.models.split(",") if model.strip()]
    results = [
        benchmark_model(
            model=model,
            prompts=prompts,
            base_url=args.base_url,
            runs=args.runs,
            num_ctx=args.num_ctx,
            timeout=args.timeout,
        )
        for model in models
    ]
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps({"results": results}, indent=2) + "\n", encoding="utf-8"
    )
    if args.markdown_out:
        _write_markdown(Path(args.markdown_out), results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
