"""Optional SMTP writer that emails the finished briefing to a Kindle address."""

from __future__ import annotations

import argparse
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path

from rollup.addons.kindle.config import (
    MAX_ATTACHMENT_BYTES,
    KindleConfig,
    smtp_env,
)
from rollup.config import Config
from rollup.models import DigestReport
from rollup.output_writers import OutputWriterError, WriteContext, requested_writer_names
from rollup.render import digest_output_stem


class KindleOutputWriter:
    """Send this run's EPUB (or HTML) to ``[kindle].to``. Failure is optional."""

    name = "kindle"
    required = False

    def register_cli(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--kindle-to",
            default=None,
            metavar="EMAIL",
            help="Send the finished briefing to this Kindle address (SMTP credentials from the environment).",
        )

    def enabled(self, args: argparse.Namespace, config: Config) -> bool:
        requested = requested_writer_names(args)
        destination = _resolve_to(args, config)
        if requested is None:
            return bool(destination)
        return "kindle" in requested

    def write(self, report: DigestReport, ctx: WriteContext) -> list[Path]:
        del report
        if ctx.dry_run:
            return []
        config = ctx.config
        kindle = config.kindle if config is not None else KindleConfig()
        to_addr = kindle.to
        sender = kindle.sender
        if not to_addr or not sender:
            if ctx.explicit_outputs:
                raise OutputWriterError(
                    "kindle writer enabled but [kindle].to and [kindle].from are required"
                )
            return []
        env = smtp_env()
        if not env["host"] or not env["password"]:
            raise OutputWriterError(
                "kindle writer needs ROLLUP_KINDLE_SMTP_HOST and "
                "ROLLUP_KINDLE_SMTP_PASSWORD in the environment"
            )
        stem = digest_output_stem(ctx.generated_at, run_id_short=ctx.run_id_short)
        epub = ctx.output_dir / f"{stem}.epub"
        html = ctx.output_dir / f"{stem}.html"
        src = epub if epub.is_file() else html
        if not src.is_file():
            raise OutputWriterError(
                f"kindle writer found no EPUB or HTML for {stem} under {ctx.output_dir}"
            )
        data = src.read_bytes()
        if len(data) > MAX_ATTACHMENT_BYTES:
            raise OutputWriterError(
                f"kindle attachment {src.name} exceeds {MAX_ATTACHMENT_BYTES} bytes"
            )
        try:
            port = int(env["port"] or "587")
        except ValueError as exc:
            raise OutputWriterError("ROLLUP_KINDLE_SMTP_PORT must be an integer") from exc
        message = EmailMessage()
        message["To"] = to_addr
        message["From"] = sender
        message["Subject"] = "Rollup briefing"
        subtype = "epub+zip" if src.suffix == ".epub" else "html"
        maintype = "application" if src.suffix == ".epub" else "text"
        message.add_attachment(
            data,
            maintype=maintype,
            subtype=subtype,
            filename=src.name,
        )
        try:
            with smtplib.SMTP(env["host"], port, timeout=30) as smtp:
                smtp.starttls(context=ssl.create_default_context())
                if env["user"]:
                    smtp.login(env["user"], env["password"] or "")
                smtp.send_message(message)
        except (OSError, smtplib.SMTPException) as exc:
            raise OutputWriterError(f"Kindle SMTP send failed: {exc}") from exc
        return [src]


def _resolve_to(args: argparse.Namespace, config: Config) -> str | None:
    raw = getattr(args, "kindle_to", None)
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return config.kindle.to
