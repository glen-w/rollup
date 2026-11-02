"""``rollup init`` — guided first-run setup that writes a sticky TOML.

The command is the answer to "mail paths are undiscoverable": it runs the same
Thunderbird profile discovery as ``rollup digest``, asks for anything it cannot
find, proposes state / output / log directories that live outside the mail
root, writes the result through :func:`rollup.config_service.apply_and_save`
(atomic, backed up, merged into any existing file), and finishes with
``rollup doctor`` so the user sees a green or red board immediately.

Invariants:

* Never writes under the mail root and refuses any path that would.
* Makes no network call unless the user explicitly asks to probe Ollama.
* Never writes secrets to TOML (there are none to write here).
* ``--yes`` makes every prompt take its default so Docker / CI can run it;
  ``--dry-run`` prints the TOML that would be written and touches nothing.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from rollup.config import (
    DEFAULT_LOG_DIR,
    DEFAULT_LOOKBACK_DAYS,
    DEFAULT_MAIL_ROOT,
    DEFAULT_OLLAMA_MODEL,
    DEFAULT_OLLAMA_URL,
    DEFAULT_OUTPUT_DIR,
    DEFAULT_RUN_PROFILE,
    DEFAULT_STATE_DIR,
)
from rollup.discovery import is_path_inside
from rollup.gmail.config import GmailConfig
from rollup.paths import discover_newsletters_sbd
from rollup.run_profiles import list_run_profiles

Prompt = Callable[[str], str]
Emit = Callable[[str], None]

EXIT_OK = 0
EXIT_ABORTED = 1
EXIT_INVALID = 2


class InitAborted(Exception):
    """User declined or gave input that cannot be used."""


@dataclass
class InitPlan:
    """Everything ``rollup init`` decided, before any write."""

    config_path: Path
    mode: str  # "mbox" | "gmail"
    root: Path | None
    mail_root: Path | None
    state_dir: Path
    output_dir: Path
    log_dir: Path
    profile: str
    lookback_days: int
    ollama: bool
    ollama_model: str | None
    gmail_label_prefix: str | None = None
    discovery_candidates: tuple[Path, ...] = ()
    notes: list[str] = field(default_factory=list)

    def sticky_values(self) -> dict[str, object]:
        values: dict[str, object] = {
            "profile": self.profile,
            "lookback_days": self.lookback_days,
            "state_dir": str(self.state_dir),
            "output_dir": str(self.output_dir),
            "log_dir": str(self.log_dir),
            "ollama": self.ollama,
        }
        if self.root is not None and self.mail_root is not None:
            values["root"] = str(self.root)
            values["mail_root"] = str(self.mail_root)
        if self.ollama and self.ollama_model:
            values["ollama_model"] = self.ollama_model
        return values


# --- prompting -----------------------------------------------------------------


class _Asker:
    """Thin prompt wrapper so ``--yes`` and tests can script every answer."""

    def __init__(self, *, prompt: Prompt, emit: Emit, assume_yes: bool) -> None:
        self._prompt = prompt
        self._emit = emit
        self.assume_yes = assume_yes

    def say(self, text: str = "") -> None:
        self._emit(text)

    def text(self, question: str, *, default: str | None) -> str:
        if self.assume_yes:
            if default is None:
                raise InitAborted(f"{question}: no default available in --yes mode")
            return default
        suffix = f" [{default}]" if default else ""
        answer = self._prompt(f"{question}{suffix}: ").strip()
        if not answer:
            if default is None:
                raise InitAborted(f"{question}: a value is required")
            return default
        return answer

    def confirm(self, question: str, *, default: bool) -> bool:
        if self.assume_yes:
            return default
        hint = "Y/n" if default else "y/N"
        answer = self._prompt(f"{question} [{hint}]: ").strip().lower()
        if not answer:
            return default
        return answer in ("y", "yes")

    def choose(self, question: str, options: list[str], *, default: str) -> str:
        if self.assume_yes:
            return default
        self.say(question)
        for i, opt in enumerate(options, 1):
            marker = "*" if opt == default else " "
            self.say(f"  {marker} {i}. {opt}")
        answer = self._prompt(f"Choice [{default}]: ").strip()
        if not answer:
            return default
        if answer.isdigit() and 1 <= int(answer) <= len(options):
            return options[int(answer) - 1]
        if answer in options:
            return answer
        raise InitAborted(f"Unknown choice {answer!r}")


# --- planning ---------------------------------------------------------------------


def _expand(raw: str | Path) -> Path:
    return Path(str(raw)).expanduser()


def _ensure_outside_mail_root(path: Path, mail_root: Path | None, label: str) -> None:
    if mail_root is None:
        return
    try:
        inside = is_path_inside(path.resolve(), mail_root.resolve())
    except OSError:
        inside = False
    if inside:
        raise InitAborted(
            f"{label} {path} is inside the mail root {mail_root}; "
            "Rollup never writes under the mail store"
        )


def _plan_mail_paths(
    ask: _Asker,
    *,
    explicit_root: Path | None,
    explicit_mail_root: Path | None,
    home: Path | None,
) -> tuple[Path | None, Path | None, tuple[Path, ...], list[str]]:
    notes: list[str] = []
    candidates: tuple[Path, ...] = ()

    if explicit_root is not None:
        root = _expand(explicit_root)
        mail_root = (
            _expand(explicit_mail_root)
            if explicit_mail_root is not None
            else (root.parent if root.name.lower().endswith(".sbd") else None)
        )
        if mail_root is None:
            mail_root = _expand(ask.text("Mail root (the account directory above the .sbd tree)", default=None))
        if not root.is_dir():
            notes.append(f"newsletter root {root} does not exist yet")
        return root, mail_root, candidates, notes

    found = discover_newsletters_sbd(home=home)
    candidates = tuple(found)
    if len(found) == 1:
        root = found[0]
        ask.say(f"Found Thunderbird newsletters at {root}")
        if ask.confirm("Use this folder tree?", default=True):
            return root, root.parent, candidates, notes
    elif len(found) > 1:
        ask.say("Several Newsletters.sbd trees were found:")
        choice = ask.choose(
            "Which one holds the newsletters you want digested?",
            [str(p) for p in found],
            default=str(found[0]),
        )
        root = Path(choice)
        return root, root.parent, candidates, notes
    else:
        ask.say("No Thunderbird Newsletters.sbd tree was discovered under ~/Library/Thunderbird.")
        ask.say("Thunderbird filters should file newsletters into folders under a Newsletters.sbd tree.")

    default_root = DEFAULT_MAIL_ROOT / "Newsletters.sbd"
    if ask.assume_yes:
        # Non-interactive: only fall back to the built-in default when it really exists.
        if default_root.is_dir():
            return default_root, default_root.parent, candidates, notes
        return None, None, candidates, notes
    raw = ask.text(
        "Path to the Newsletters.sbd directory (or leave blank to skip local mail)",
        default="",
    )
    if not raw:
        return None, None, candidates, notes
    root = _expand(raw)
    mail_root = root.parent if root.name.lower().endswith(".sbd") else _expand(
        ask.text("Mail root (the account directory above the .sbd tree)", default=str(root.parent))
    )
    if not root.is_dir():
        notes.append(f"newsletter root {root} does not exist yet")
    return root, mail_root, candidates, notes


def _probe_ollama(url: str, model: str) -> tuple[bool, str]:
    from rollup.llm_client import check_ollama_available

    return check_ollama_available(url, model)


def build_plan(
    args: argparse.Namespace,
    *,
    prompt: Prompt = input,
    emit: Emit = print,
    home: Path | None = None,
    ollama_probe: Callable[[str, str], tuple[bool, str]] = _probe_ollama,
) -> InitPlan:
    """Interactively (or by defaults) assemble an :class:`InitPlan`."""
    from rollup.config_service import resolve_config_path

    ask = _Asker(prompt=prompt, emit=emit, assume_yes=bool(getattr(args, "yes", False)))
    config_path = resolve_config_path(explicit=getattr(args, "config", None))

    ask.say("Rollup setup")
    ask.say(f"Config file: {config_path}")
    ask.say("")

    explicit_root = getattr(args, "root", None)
    explicit_mail_root = getattr(args, "mail_root", None)
    gmail_prefix = getattr(args, "gmail_label_prefix", None)

    root: Path | None
    mail_root: Path | None
    candidates: tuple[Path, ...]
    notes: list[str]
    if gmail_prefix and not explicit_root:
        # Explicit Gmail setup without a local tree: skip discovery entirely.
        root, mail_root, candidates, notes = None, None, (), []
    else:
        root, mail_root, candidates, notes = _plan_mail_paths(
            ask,
            explicit_root=Path(explicit_root) if explicit_root else None,
            explicit_mail_root=Path(explicit_mail_root) if explicit_mail_root else None,
            home=home,
        )

    mode = "mbox"
    if root is None:
        if gmail_prefix is None and not ask.assume_yes:
            if ask.confirm("Read newsletters from Gmail labels instead (read-only OAuth)?", default=False):
                gmail_prefix = ask.text("Gmail label prefix", default="Newsletters/")
        if gmail_prefix is None:
            raise InitAborted(
                "Nothing to digest: give --root (Thunderbird .sbd) or --gmail-label-prefix"
            )
        mode = "gmail"
        notes.append("Gmail-only setup: run `rollup gmail auth` before the first digest")

    # Writable directories: propose, confirm, and refuse anything inside the mail root.
    state_dir = _expand(ask.text("State directory (SQLite, lock, manifests)", default=str(getattr(args, "state_dir", None) or DEFAULT_STATE_DIR)))
    output_dir = _expand(ask.text("Output directory (digests)", default=str(getattr(args, "output_dir", None) or DEFAULT_OUTPUT_DIR)))
    log_dir = _expand(ask.text("Log directory", default=str(getattr(args, "log_dir", None) or DEFAULT_LOG_DIR)))
    for label, path in (("state_dir", state_dir), ("output_dir", output_dir), ("log_dir", log_dir)):
        _ensure_outside_mail_root(path, mail_root, label)

    profiles = [p.name for p in list_run_profiles()] or [DEFAULT_RUN_PROFILE]
    profile = ask.choose(
        "Run profile",
        profiles,
        default=str(getattr(args, "profile", None) or DEFAULT_RUN_PROFILE),
    )
    if profile not in profiles:
        raise InitAborted(f"Unknown run profile {profile!r} (choose from {', '.join(profiles)})")
    lookback_arg = getattr(args, "lookback_days", None)
    lookback_raw = ask.text(
        "Lookback window in days",
        default=str(lookback_arg if lookback_arg is not None else DEFAULT_LOOKBACK_DAYS),
    )
    try:
        lookback_days = int(lookback_raw)
    except ValueError as exc:
        raise InitAborted(f"lookback_days must be an integer, got {lookback_raw!r}") from exc
    if lookback_days < 1:
        raise InitAborted("lookback_days must be at least 1")

    ollama = bool(getattr(args, "ollama", False))
    ollama_model: str | None = getattr(args, "ollama_model", None)
    if not ollama and not getattr(args, "no_ollama", False):
        ollama = ask.confirm(
            "Summarise with a local Ollama model? (default: preview summaries, no network)",
            default=False,
        )
    if ollama:
        ollama_model = ask.text("Ollama model", default=ollama_model or DEFAULT_OLLAMA_MODEL)
        if ask.confirm("Probe the local Ollama server now (one loopback HTTP call)?", default=False):
            ok, message = ollama_probe(DEFAULT_OLLAMA_URL, ollama_model)
            notes.append(f"ollama probe: {'ok' if ok else 'failed'} — {message}")
        else:
            notes.append("ollama not probed; `rollup doctor --network` will check it")

    return InitPlan(
        config_path=config_path,
        mode=mode,
        root=root,
        mail_root=mail_root,
        state_dir=state_dir,
        output_dir=output_dir,
        log_dir=log_dir,
        profile=profile,
        lookback_days=lookback_days,
        ollama=ollama,
        ollama_model=ollama_model if ollama else None,
        gmail_label_prefix=gmail_prefix if mode == "gmail" else None,
        discovery_candidates=candidates,
        notes=notes,
    )


# --- rendering and writing -----------------------------------------------------


def render_plan_toml(plan: InitPlan) -> str:
    """The TOML ``init`` would write to a fresh file (for --dry-run and review)."""
    import tomlkit

    from rollup.config_service import _apply_patch_to_doc
    from rollup.user_config import LoadedUserConfig

    doc = tomlkit.document()
    _apply_patch_to_doc(doc, _patch_for(plan), LoadedUserConfig())
    return tomlkit.dumps(doc)


def _patch_for(plan: InitPlan):
    from rollup.config_service import ConfigPatch

    patch = ConfigPatch(values=plan.sticky_values())
    if plan.mode == "gmail":
        patch.gmail = GmailConfig(enabled=True, label_prefix=plan.gmail_label_prefix)
    return patch


def write_plan(plan: InitPlan) -> Path:
    """Merge the plan into the config file atomically (existing keys are preserved)."""
    from rollup.config_service import apply_and_save, compute_revision

    path = plan.config_path
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = apply_and_save(
        path,
        _patch_for(plan),
        expected_revision=compute_revision(path),
    )
    return doc.path


def _run_doctor(plan: InitPlan, emit: Emit) -> bool:
    from rollup.cli import build_parser, cmd_doctor

    argv = ["--config", str(plan.config_path), "doctor"]
    parser = build_parser()
    args = parser.parse_args(argv[2:])
    # Apply the freshly written sticky values exactly as main() would.
    from rollup.cli import _apply_loaded_config, _apply_path_discovery
    from rollup.user_config import load_user_config

    loaded = load_user_config(explicit_path=str(plan.config_path))
    args._loaded_user_config = loaded  # noqa: SLF001
    _apply_loaded_config(args, loaded, argv)
    _apply_path_discovery(args, loaded, argv)
    emit("")
    emit("Running rollup doctor…")
    return cmd_doctor(args) == 0


def _next_steps(plan: InitPlan) -> list[str]:
    steps = []
    if plan.mode == "gmail":
        steps.append("rollup gmail auth        # one-time read-only OAuth")
    steps.append("rollup digest             # first briefing (preview summaries unless ollama=true)")
    steps.append("rollup web                # loopback UI at http://127.0.0.1:8765")
    steps.append("rollup cron install       # or see docs/CRON.md / docs/DOCKER.md for scheduling")
    return steps


def cmd_init(
    args: argparse.Namespace,
    *,
    prompt: Prompt = input,
    emit: Emit = print,
    home: Path | None = None,
    ollama_probe: Callable[[str, str], tuple[bool, str]] = _probe_ollama,
) -> int:
    try:
        plan = build_plan(args, prompt=prompt, emit=emit, home=home, ollama_probe=ollama_probe)
    except InitAborted as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_INVALID
    except (KeyboardInterrupt, EOFError):
        print("\nSetup cancelled; nothing was written.", file=sys.stderr)
        return EXIT_ABORTED

    emit("")
    emit("Plan")
    emit(f"  mode:        {plan.mode}")
    if plan.root is not None:
        emit(f"  root:        {plan.root}")
        emit(f"  mail_root:   {plan.mail_root}")
    if plan.gmail_label_prefix:
        emit(f"  gmail:       labels under {plan.gmail_label_prefix!r}")
    emit(f"  state_dir:   {plan.state_dir}")
    emit(f"  output_dir:  {plan.output_dir}")
    emit(f"  log_dir:     {plan.log_dir}")
    emit(f"  profile:     {plan.profile} ({plan.lookback_days}-day window)")
    emit(f"  summaries:   {'ollama ' + str(plan.ollama_model) if plan.ollama else 'preview (no network)'}")
    for note in plan.notes:
        emit(f"  note:        {note}")

    if getattr(args, "dry_run", False):
        emit("")
        emit(f"# would write to {plan.config_path}")
        emit(render_plan_toml(plan).rstrip())
        return EXIT_OK

    ask = _Asker(prompt=prompt, emit=emit, assume_yes=bool(getattr(args, "yes", False)))
    existed = plan.config_path.is_file()
    verb = "Update" if existed else "Write"
    try:
        if not ask.confirm(f"{verb} {plan.config_path}?", default=True):
            emit("Nothing written.")
            return EXIT_ABORTED
    except (KeyboardInterrupt, EOFError):
        print("\nSetup cancelled; nothing was written.", file=sys.stderr)
        return EXIT_ABORTED

    try:
        written = write_plan(plan)
    except Exception as exc:
        print(f"ERROR: could not write config: {exc}", file=sys.stderr)
        return EXIT_INVALID
    emit(f"{'Updated' if existed else 'Wrote'} {written}")

    doctor_ok = True
    if not getattr(args, "skip_doctor", False):
        doctor_ok = _run_doctor(plan, emit)

    emit("")
    emit("Next steps")
    for step in _next_steps(plan):
        emit(f"  {step}")
    return EXIT_OK if doctor_ok else EXIT_PARTIAL_DOCTOR


EXIT_PARTIAL_DOCTOR = 2
