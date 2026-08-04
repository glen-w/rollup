# XTEINK Optimized Output

XTEINK is an in-tree **output writer addon**: it writes a Markdown digest optimized
for Xteink e-ink readers. Default Markdown/HTML digests stay in core; XTEINK
attaches after the report is built. For a rich offline ebook, use the `epub`
writer — there is no separate XTEINK HTML file.

## Features

- **High-contrast Markdown** (short lines, clear headings) for e-ink readability
- **Short line lengths** (~60 characters)
- **No external URLs** — markdown links keep their labels; destinations are stripped
- **Clear section breaks** and simple TOC suitable for offline navigation
- **Folder theme emoji** from `[folders.*]` when configured

## Usage

XTEINK is included in the default writer set. To write only XTEINK beside
Markdown/HTML:

```bash
# Normal digest + XTEINK-optimized sibling Markdown
rollup digest --xteink --lookback-days 7

# Same via the generic output-writer flag
rollup digest --output xteink --lookback-days 7

# Compatibility alias (same as --xteink)
rollup digest --x3 --lookback-days 7

# With local Ollama summaries
rollup digest --xteink --ollama --lookback-days 7

# Dry-run: parses the digest but does not write XTEINK (or normal) outputs
rollup digest --xteink --dry-run --lookback-days 7
```

`--x3` remains a compatibility alias for `--output xteink` / `--xteink`. Selecting
any `--output` / `--xteink` replaces the default-all writer set. Files are skipped
under `--dry-run`.

## Output files

XTEINK uses the normal digest stem with a `.xteink` variant suffix:

- `2026-07-02T103000Z-newsletter-digest.xteink.md`

## Example

```bash
rollup digest --xteink --ollama --effort high --lookback-days 7
```

## Third-party writers

Packages can register additional writers via the `rollup.output_writers` entry
point group (see README). Built-in names: `xteink`, `txt`, `json`, `epub` — see
[OUTPUT_WRITERS.md](OUTPUT_WRITERS.md).
