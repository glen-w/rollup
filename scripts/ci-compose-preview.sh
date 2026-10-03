#!/usr/bin/env bash
# Wave B stranger path: Compose preview digest, no Ollama, no override.yml.
# Job name in CI: compose-preview
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root"
compose=(docker compose -f docker-compose.ci.yml)
artifact="${CI_ARTIFACT_DIR:-$root/ci-artifact}"
mkdir -p "$artifact"

"${compose[@]}" build

echo "== rollup --help =="
"${compose[@]}" run --rm --no-deps rollup --help >/dev/null

echo "== rollup doctor =="
"${compose[@]}" run --rm --no-deps rollup \
  --config /fixtures/empty_config.toml \
  doctor \
  --root /fixtures/Newsletters.sbd \
  --mail-root /fixtures \
  --output-dir /tmp/doctor-out \
  --state-dir /tmp/doctor-state \
  --log-dir /tmp/doctor-logs

run_digest() {
  local out_dir="$1"
  shift
  mkdir -p "$out_dir"
  chmod 777 "$out_dir"
  local stdout="$out_dir/stdout.txt"
  local stderr="$out_dir/stderr.txt"
  set +e
  "${compose[@]}" run --rm --no-deps -v "$out_dir:/out" rollup \
    --config /fixtures/empty_config.toml \
    digest \
    --no-ollama --no-linkedin --no-reddit --no-webpage --no-grouping \
    --output none \
    --output-dir /out \
    --state-dir /tmp/state \
    --log-dir /tmp/logs \
    "$@" \
    >"$stdout" 2>"$stderr"
  local rc=$?
  set -e
  echo "$rc"
}

echo "== happy preview =="
happy="$(mktemp -d)"
happy_rc="$(run_digest "$happy" \
  --lookback-days 4000 \
  --root /fixtures/Newsletters.sbd \
  --mail-root /fixtures)"
if [[ "$happy_rc" != "0" ]]; then
  echo "happy path failed rc=$happy_rc" >&2
  cat "$happy/stderr.txt" >&2 || true
  exit 1
fi
grep -q "no_ollama=True" "$happy/stderr.txt"
md="$(find "$happy" -name '*-newsletter-digest.md' | head -1)"
if [[ -z "${md}" ]]; then
  echo "happy path wrote no digest" >&2
  exit 1
fi
grep -q "Quick thoughts on learning" "$md"
# Preview is the default digest mode (not --dry-run): a dated file exists.
head -c 262144 "$md" >"$artifact/preview-digest.md"
size="$(wc -c <"$artifact/preview-digest.md" | tr -d ' ')"
if [[ "$size" -gt 262144 ]]; then
  echo "artifact ${size} bytes exceeds 256KB" >&2
  exit 1
fi

echo "== empty window =="
empty="$(mktemp -d)"
empty_rc="$(run_digest "$empty" \
  --lookback-days 7 \
  --root /fixtures/ci/empty-window/Newsletters.sbd \
  --mail-root /fixtures/ci/empty-window)"
if [[ "$empty_rc" != "0" ]]; then
  echo "empty window failed rc=$empty_rc" >&2
  cat "$empty/stderr.txt" >&2 || true
  exit 1
fi
grep -q "empty_window:" "$empty/stderr.txt"

echo "== unreadable store =="
bad="$(mktemp -d)"
set +e
bad_rc="$(run_digest "$bad" \
  --lookback-days 7 \
  --root /fixtures/ci/corrupt/Newsletters.sbd \
  --mail-root /fixtures/ci/corrupt)"
set -e
if [[ "$bad_rc" == "0" ]]; then
  echo "corrupt store exited 0" >&2
  exit 1
fi
grep -q "store_unreadable:" "$bad/stderr.txt"
if grep -q "empty_window:" "$bad/stderr.txt"; then
  echo "corrupt store was labeled empty_window" >&2
  exit 1
fi

echo "compose-preview ok ($(wc -c <"$artifact/preview-digest.md" | tr -d ' ') bytes)"
