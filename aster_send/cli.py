# SPDX-License-Identifier: MIT
import argparse
import getpass
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

from . import __version__, config, message, presets, transport
from .token import parse_aster_token, redact

COMMANDS = ("send", "setup", "run", "config", "doctor", "profiles", "version")
DEFAULT_OUTPUT_LINES = 40

USAGE_EXAMPLES = """The send command is the default, so an address can come first:
  aster-send you@example.com -s "Deploy finished" -m "Version 2.1 is live."
  df -h | aster-send you@example.com -s "Disk usage"
  aster-send run --to you@example.com -- ./backup.sh

Start with 'aster-send setup', and check it with 'aster-send doctor'."""



def positive_seconds(value: str) -> float:
    try:
        number = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"'{value}' is not a number") from None

    if number <= 0:
        raise argparse.ArgumentTypeError("must be greater than 0")

    return number


def positive_count(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"'{value}' is not a whole number") from None

    if number < 1:
        raise argparse.ArgumentTypeError("must be at least 1")

    return number


def prompt_secret(label: str) -> str:
    if sys.stdin is not None and sys.stdin.isatty():
        try:
            return getpass.getpass(label).strip()
        except (EOFError, getpass.GetPassWarning):
            pass

    return input(label).strip()


def command_label(command: list[str]) -> str:
    program = Path(command[0]).name or command[0]

    if program.lower().endswith(".exe"):
        program = program[:-4]

    return program or "command"


def report(text: str = "") -> None:
    print(text, flush=True)


def progress(text: str, quiet: bool = False) -> None:
    if not quiet:
        print(text, file=sys.stderr, flush=True)


def fail(text: str, hint: str = "") -> int:
    print(f"error: {text}", file=sys.stderr)

    if hint:
        print(f"hint: {hint}", file=sys.stderr)

    return 1


def example_command(recipient: str) -> str:
    if os.name == "nt":
        return f'aster-send {recipient} -s "Hello from aster-send" -m "it works"'

    return f"echo 'it works' | aster-send {recipient} -s 'Hello from aster-send'"


def read_stdin_body() -> str:
    if sys.stdin is None or sys.stdin.isatty():
        return ""

    return sys.stdin.read()


def resolve_profile(settings: config.configuration, name: str) -> config.profile:
    entry = settings.get(name)

    if not entry.address:
        raise config.config_error(f"profile '{entry.name}' has no address configured")

    if not entry.secret:
        raise config.config_error(
            f"no password stored for profile '{entry.name}', run 'aster-send setup' again"
        )

    return entry


def resolve_recipients(entry: config.profile, values: list[str]) -> list[str]:
    recipients = message.split_recipients(values)

    if recipients:
        return recipients

    if entry.default_to:
        return message.split_recipients([entry.default_to])

    return []


def command_setup(args: argparse.Namespace) -> int:
    settings = config.load()

    try:
        name = config.validate_profile_name(args.profile or "default")
    except config.config_error as error:
        return fail(str(error))

    report("Setting up a profile. Values in brackets are the defaults.")
    report()

    secret = prompt_secret("SMTP password or Aster token (hidden): ")

    if not secret:
        return fail("a password or token is required")

    parsed = parse_aster_token(secret)

    if parsed is not None:
        report(f"Recognized an Aster token, using {parsed.host}:{parsed.port} as {parsed.username}.")
        host = parsed.host
        port = parsed.port
        security = parsed.security
        username = parsed.username
    else:
        host = input("SMTP host: ").strip()

        if not host:
            return fail("an SMTP host is required")

        port_value = input("SMTP port [587]: ").strip() or "587"

        if not port_value.isdigit():
            return fail(f"invalid port: {port_value}")

        port = int(port_value)
        security = (input("Security, starttls or ssl [starttls]: ").strip() or "starttls").lower()
        username = input("SMTP username: ").strip()

    address = input("Send from address: ").strip()

    if not address:
        return fail("a from address is required")

    try:
        message.validate_address(address)
    except message.message_error as error:
        return fail(str(error))

    display_name = input("Display name (optional): ").strip()
    default_to = input("Default recipient (optional): ").strip()

    entry = config.profile(
        name=name,
        address=address,
        secret=secret,
        display_name=display_name,
        host=host,
        port=port,
        security=security,
        username=username or address,
        default_to=default_to,
    )

    settings.profiles[name] = entry

    if not settings.default_profile:
        settings.default_profile = name

    stored_in_keyring = config.write_keyring_secret(name, secret)

    if stored_in_keyring:
        entry.secret = ""

    path = config.save(settings)
    entry.secret = secret

    report()
    report(f"Saved profile '{name}' to {path}")

    if stored_in_keyring:
        report("The password is in your system keychain.")
    else:
        report("The password is in the config file, which is restricted to your user account.")
        report("To use your system keychain instead, install keyring and run setup again.")

    report()
    report("Send a test message with:")
    report(f"  {example_command(default_to or address)}")

    return 0


def command_send(args: argparse.Namespace) -> int:
    settings = config.load()

    try:
        entry = resolve_profile(settings, args.profile)
    except config.config_error as error:
        return fail(str(error))

    recipients = resolve_recipients(entry, args.recipients)

    if not recipients:
        return fail(
            "no recipients given",
            "pass an address, or set a default recipient with 'aster-send setup'",
        )

    for value in recipients:
        if "@" not in value:
            return fail(
                f"invalid email address: {value}",
                "an address looks like name@example.com, and 'aster-send --help' lists the commands",
            )

    entry.allow_plaintext_auth = entry.allow_plaintext_auth or args.allow_plaintext_auth
    body = args.body if args.body is not None else read_stdin_body()
    html = ""

    if not body and not args.quiet:
        progress("Warning: the body is empty, pass -m or pipe text in.")

    if args.html:
        html_path = Path(args.html)

        if not html_path.is_file():
            return fail(f"html file not found: {html_path}")

        html = html_path.read_text(encoding="utf-8")

    cc = message.split_recipients(args.cc)
    bcc = message.split_recipients(args.bcc)

    try:
        payload = message.build(
            sender=entry.address,
            recipients=recipients,
            subject=args.subject,
            body=body,
            display_name=entry.display_name,
            html=html,
            cc=cc,
            bcc=bcc,
            reply_to=args.reply_to,
            attachments=[Path(item) for item in args.attach],
        )
    except message.message_error as error:
        return fail(str(error))

    envelope = message.envelope_recipients(recipients, cc, bcc)

    if args.dry_run:
        report(payload.as_string())
        report(f"[dry run] would send to {', '.join(envelope)} through {entry.host}:{entry.port}")

        return 0

    progress(f"Connecting to {entry.host}:{entry.port} ...", args.quiet)

    try:
        transport.send(
            entry,
            payload,
            envelope,
            timeout=args.timeout,
            attempts=args.attempts,
            notify=lambda text: progress(text, args.quiet),
        )
    except transport.transport_error as error:
        return fail(error.detail, error.hint)

    if not args.quiet:
        report(f"Sent to {', '.join(envelope)}")

    return 0


def tail_lines(text: str, limit: int) -> str:
    lines = text.splitlines()

    if limit <= 0 or len(lines) <= limit:
        return text

    hidden = len(lines) - limit

    return "\n".join([f"[{hidden} earlier lines omitted]"] + lines[-limit:])


def strip_separator(command: list[str]) -> list[str]:
    if command and command[0] == "--":
        return command[1:]

    return command


def command_run(args: argparse.Namespace) -> int:
    command = strip_separator(args.command)

    if not command:
        return fail("no command given", "use 'aster-send run -- your-command --flags'")

    settings = config.load()

    try:
        entry = resolve_profile(settings, args.profile)
    except config.config_error as error:
        return fail(str(error))

    recipients = resolve_recipients(entry, args.to)

    if not recipients:
        return fail(
            "no recipients given",
            "pass --to, or set a default recipient with 'aster-send setup'",
        )

    printable = " ".join(shlex.quote(part) for part in command)
    started = time.monotonic()

    try:
        completed = subprocess.run(command, capture_output=True, text=True)
    except OSError as error:
        return fail(
            f"cannot run {command[0]}: {error.strerror or error}",
            "check that the program exists and is on your PATH",
        )

    elapsed = time.monotonic() - started
    succeeded = completed.returncode == 0

    sys.stdout.write(completed.stdout)
    sys.stderr.write(completed.stderr)

    if succeeded and not args.always:
        return completed.returncode

    status = "finished" if succeeded else f"failed with exit code {completed.returncode}"
    subject = f"{args.subject_prefix} {command_label(command)} {status}".strip()
    sections = [
        f"Command: {printable}",
        f"Status: {status}",
        f"Duration: {elapsed:.1f}s",
    ]

    if completed.stdout.strip():
        sections.append(f"\nOutput:\n{tail_lines(completed.stdout.rstrip(), args.max_lines)}")

    if completed.stderr.strip():
        sections.append(f"\nErrors:\n{tail_lines(completed.stderr.rstrip(), args.max_lines)}")

    payload = message.build(
        sender=entry.address,
        recipients=recipients,
        subject=subject,
        body="\n".join(sections) + "\n",
        display_name=entry.display_name,
    )

    progress(f"Mailing the result to {', '.join(recipients)} ...")

    try:
        transport.send(
            entry,
            payload,
            message.envelope_recipients(recipients, None, None),
            timeout=args.timeout,
            attempts=args.attempts,
            notify=progress,
        )
    except transport.transport_error as error:
        fail(error.detail, error.hint)

    return completed.returncode


def command_config(args: argparse.Namespace) -> int:
    if args.list or not args.app:
        report("Available configurations:")

        for item in presets.PRESETS:
            report(f"  {item.key:<16} {item.title}")

        report()
        report("Print one with: aster-send config <name>")

        return 0

    if args.app not in presets.PRESETS_BY_KEY:
        known = ", ".join(sorted(presets.PRESETS_BY_KEY))

        return fail(f"unknown configuration '{args.app}'", f"available: {known}")

    settings = config.load()

    try:
        entry = settings.get(args.profile)
    except config.config_error as error:
        return fail(str(error))

    values = presets.build_values(
        host=entry.host,
        port=entry.port,
        security=entry.security,
        username=entry.resolved_username(),
        address=entry.address,
        recipient=entry.default_to,
        secret=entry.secret if args.reveal else "",
    )

    item = presets.PRESETS_BY_KEY[args.app]
    report(f"# {item.title}")
    report(presets.render(args.app, values))

    if not args.reveal:
        report()
        report(f"# Replace {presets.SECRET_PLACEHOLDER} with your password, or pass --reveal")

    return 0


def command_doctor(args: argparse.Namespace) -> int:
    settings = config.load()
    report(f"Config file: {settings.path}")

    try:
        entry = resolve_profile(settings, args.profile)
    except config.config_error as error:
        return fail(str(error))

    report(f"Profile:     {entry.name}")
    report(f"From:        {entry.address}")
    report(f"Server:      {entry.host}:{entry.port} ({entry.security})")
    report(f"Username:    {entry.resolved_username()}")
    report(f"Password:    {redact(entry.secret)}")
    report()
    report(f"Connecting to {entry.host}:{entry.port}, timeout {args.timeout:.0f}s ...")

    try:
        transport.verify(entry, timeout=args.timeout, notify=report)
    except transport.transport_error as error:
        return fail(error.detail, error.hint)

    report("Connected and authenticated successfully.")

    return 0


def command_profiles(args: argparse.Namespace) -> int:
    settings = config.load()

    if not settings.profiles:
        report("No profiles configured yet. Run 'aster-send setup' to add one.")

        return 0

    for name in sorted(settings.profiles):
        entry = settings.profiles[name]
        marker = "*" if name == settings.default_profile else " "
        report(f"{marker} {name:<12} {entry.address:<32} {entry.host}:{entry.port}")

    return 0


def command_version(args: argparse.Namespace) -> int:
    report(f"aster-send {__version__}")

    return 0


def add_send_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("recipients", nargs="*", help="one or more recipient addresses")
    parser.add_argument("-s", "--subject", default="", help="message subject")
    parser.add_argument("-m", "--body", help="message body, otherwise read from stdin")
    parser.add_argument("-a", "--attach", action="append", default=[], help="attach a file")
    parser.add_argument("--html", help="path to an html alternative body")
    parser.add_argument("--cc", action="append", default=[], help="carbon copy recipient")
    parser.add_argument("--bcc", action="append", default=[], help="blind carbon copy recipient")
    parser.add_argument("--reply-to", default="", help="reply-to address")
    parser.add_argument(
        "--dry-run", action="store_true", help="print the message instead of sending it"
    )
    parser.add_argument("-q", "--quiet", action="store_true", help="print nothing on success")
    parser.add_argument(
        "--allow-plaintext-auth",
        action="store_true",
        help="permit authentication over an unencrypted connection",
    )
    parser.add_argument(
        "--timeout",
        type=positive_seconds,
        default=transport.DEFAULT_TIMEOUT,
        help="socket timeout in seconds",
    )
    parser.add_argument(
        "--attempts",
        type=positive_count,
        default=transport.DEFAULT_ATTEMPTS,
        help="attempts for transient failures",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aster-send",
        description="Send email from the command line through any SMTP server.",
        epilog=USAGE_EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("-p", "--profile", default="", help="profile to use")
    subparsers = parser.add_subparsers(dest="command")

    send_parser = subparsers.add_parser("send", help="send a message")
    add_send_arguments(send_parser)
    send_parser.set_defaults(handler=command_send)

    setup_parser = subparsers.add_parser("setup", help="create or update a profile")
    setup_parser.set_defaults(handler=command_setup)

    run_parser = subparsers.add_parser("run", help="run a command and mail the result")
    run_parser.add_argument("--to", action="append", default=[], help="recipient address")
    run_parser.add_argument("--always", action="store_true", help="mail on success as well")
    run_parser.add_argument("--subject-prefix", default="[aster-send]", help="subject prefix")
    run_parser.add_argument(
        "--max-lines", type=positive_count, default=DEFAULT_OUTPUT_LINES, help="output lines to include"
    )
    run_parser.add_argument(
        "--timeout",
        type=positive_seconds,
        default=transport.DEFAULT_TIMEOUT,
        help="socket timeout in seconds",
    )
    run_parser.add_argument(
        "--attempts",
        type=positive_count,
        default=transport.DEFAULT_ATTEMPTS,
        help="attempts for transient failures",
    )
    run_parser.add_argument("command", nargs=argparse.REMAINDER, help="the command to run")
    run_parser.set_defaults(handler=command_run)

    config_parser = subparsers.add_parser("config", help="print settings for another app")
    config_parser.add_argument("app", nargs="?", default="", help="the app to configure")
    config_parser.add_argument("--list", action="store_true", help="list available apps")
    config_parser.add_argument("--reveal", action="store_true", help="include the real password")
    config_parser.set_defaults(handler=command_config)

    doctor_parser = subparsers.add_parser("doctor", help="check the profile and the connection")
    doctor_parser.add_argument(
        "--timeout",
        type=positive_seconds,
        default=transport.DEFAULT_TIMEOUT,
        help="socket timeout in seconds",
    )
    doctor_parser.set_defaults(handler=command_doctor)

    profiles_parser = subparsers.add_parser("profiles", help="list configured profiles")
    profiles_parser.set_defaults(handler=command_profiles)

    version_parser = subparsers.add_parser("version", help="print the version")
    version_parser.set_defaults(handler=command_version)

    return parser


def normalize(argv: list[str]) -> list[str]:
    skip_next = False

    for index, value in enumerate(argv):
        if skip_next:
            skip_next = False
            continue

        if value in COMMANDS:
            return argv

        if value in {"-h", "--help"}:
            return argv

        if value in {"-p", "--profile"}:
            skip_next = True
            continue

        if value.startswith("--profile="):
            continue

        return argv[:index] + ["send"] + argv[index:]

    return argv


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()

    if not arguments:
        parser.print_help()

        return 2

    parsed = parser.parse_args(normalize(arguments))

    if not getattr(parsed, "handler", None):
        parser.print_help()

        return 2

    try:
        return parsed.handler(parsed)
    except KeyboardInterrupt:
        print("\ncanceled", file=sys.stderr, flush=True)

        return 130
    except (config.config_error, message.message_error) as error:
        return fail(str(error))
    except transport.transport_error as error:
        return fail(error.detail, error.hint)
    except ValueError as error:
        return fail(str(error))
    except OSError as error:
        return fail(str(error))


if __name__ == "__main__":
    raise SystemExit(main())
