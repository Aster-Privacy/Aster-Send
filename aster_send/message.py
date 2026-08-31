# SPDX-License-Identifier: MIT
import mimetypes
from email.headerregistry import Address
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr
from pathlib import Path

MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024


class message_error(Exception):
    pass


def split_recipients(values: list[str]) -> list[str]:
    recipients = []

    for value in values:
        for candidate in value.replace(";", ",").split(","):
            cleaned = candidate.strip()

            if cleaned and cleaned not in recipients:
                recipients.append(cleaned)

    return recipients


def validate_address(value: str) -> str:
    _, parsed = parseaddr(value)

    if not parsed or parsed.count("@") != 1:
        raise message_error(f"invalid email address: {value}")

    local_part, _, domain = parsed.partition("@")

    if not local_part or not domain or "." not in domain:
        raise message_error(f"invalid email address: {value}")

    if any(character.isspace() or character < " " or character == "" for character in parsed):
        raise message_error(f"invalid email address: {value}")

    if "<" not in value and parsed != value.strip():
        raise message_error(f"invalid email address: {value}")

    return value


def format_sender(address: str, display_name: str) -> str:
    if not display_name:
        return address

    local_part, _, domain = address.partition("@")

    return str(Address(display_name=display_name, username=local_part, domain=domain))


def attach_file(message: EmailMessage, path: Path) -> None:
    if path.is_dir():
        raise message_error(f"attachment is a folder, not a file: {path}")

    if not path.is_file():
        raise message_error(f"attachment not found: {path}")

    size = path.stat().st_size

    if size > MAX_ATTACHMENT_BYTES:
        limit = MAX_ATTACHMENT_BYTES // (1024 * 1024)
        raise message_error(f"attachment {path.name} is larger than {limit} MB")

    guessed, _ = mimetypes.guess_type(path.name)
    main_type, _, sub_type = (guessed or "application/octet-stream").partition("/")

    message.add_attachment(
        path.read_bytes(),
        maintype=main_type,
        subtype=sub_type or "octet-stream",
        filename=path.name,
    )


def build(
    sender: str,
    recipients: list[str],
    subject: str,
    body: str,
    display_name: str = "",
    html: str = "",
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    reply_to: str = "",
    attachments: list[Path] | None = None,
) -> EmailMessage:
    if not recipients:
        raise message_error("at least one recipient is required")

    validate_address(sender)

    for recipient in recipients + (cc or []) + (bcc or []):
        validate_address(recipient)

    message = EmailMessage()
    message["From"] = format_sender(sender, display_name)
    message["To"] = ", ".join(recipients)
    message["Subject"] = subject
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = make_msgid(domain=sender.rpartition("@")[2] or None)

    if cc:
        message["Cc"] = ", ".join(cc)

    if reply_to:
        message["Reply-To"] = validate_address(reply_to)

    message.set_content(body or "")

    if html:
        message.add_alternative(html, subtype="html")

    for path in attachments or []:
        attach_file(message, path)

    return message


def envelope_recipients(
    recipients: list[str], cc: list[str] | None, bcc: list[str] | None
) -> list[str]:
    combined = []

    for value in recipients + (cc or []) + (bcc or []):
        _, parsed = parseaddr(value)

        if parsed and parsed not in combined:
            combined.append(parsed)

    return combined
