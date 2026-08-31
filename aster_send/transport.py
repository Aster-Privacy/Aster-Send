# SPDX-License-Identifier: MIT
import smtplib
import socket
import ssl
import time
from contextlib import contextmanager
from email.message import EmailMessage

from .config import profile

DEFAULT_TIMEOUT = 20.0
DEFAULT_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 2.0
TRANSIENT_STATUS_CODES = {421, 450, 451, 452, 454}
TLS_SECURITY_MODES = frozenset({"ssl", "tls", "smtps"})
PLAINTEXT_SECURITY_MODES = frozenset({"none", "plain", "insecure"})
KNOWN_SECURITY_MODES = TLS_SECURITY_MODES | PLAINTEXT_SECURITY_MODES | {"starttls"}


class transport_error(Exception):
    def __init__(self, detail: str, hint: str = "", transient: bool = False):
        super().__init__(detail)
        self.detail = detail
        self.hint = hint
        self.transient = transient


def local_hostname() -> str:
    try:
        name = socket.gethostname().strip()
    except OSError:
        name = ""

    if "." in name and name.isascii() and " " not in name:
        return name

    address = "127.0.0.1"

    if name:
        try:
            address = socket.gethostbyname(name)
        except OSError:
            address = "127.0.0.1"

    return f"[{address}]"


def describe_auth_failure(entry: profile, code: int) -> str:
    if entry.host.endswith("astermail.org"):
        return (
            "the token is wrong or no longer valid, generate a new one in Settings > "
            "Bridge and run aster-send setup again"
        )

    return f"the server rejected the credentials with code {code}"


@contextmanager
def connect(entry: profile, timeout: float = DEFAULT_TIMEOUT, notify=None):
    security = (entry.security or "starttls").lower()

    if security not in KNOWN_SECURITY_MODES:
        raise transport_error(
            f"unknown security mode '{entry.security}'",
            "use starttls, ssl, or none",
        )

    if security in PLAINTEXT_SECURITY_MODES and entry.secret and not entry.allow_plaintext_auth:
        raise transport_error(
            "refusing to send a password over an unencrypted connection",
            "use starttls or ssl, or pass --allow-plaintext-auth if the server is local",
        )

    context = ssl.create_default_context()
    client = None

    try:
        if security in TLS_SECURITY_MODES:
            client = smtplib.SMTP_SSL(
                entry.host,
                entry.port,
                timeout=timeout,
                context=context,
                local_hostname=local_hostname(),
            )
        else:
            client = smtplib.SMTP(
                entry.host, entry.port, timeout=timeout, local_hostname=local_hostname()
            )
            client.ehlo()

            if security == "starttls":
                client.starttls(context=context)
                client.ehlo()

        if entry.secret:
            if notify is not None:
                notify(f"Authenticating as {entry.resolved_username()} ...")

            client.login(entry.resolved_username(), entry.secret)

        yield client
    except smtplib.SMTPAuthenticationError as error:
        raise transport_error(
            "authentication failed",
            describe_auth_failure(entry, error.smtp_code),
        ) from error
    except smtplib.SMTPNotSupportedError as error:
        raise transport_error(
            str(error),
            "the server does not support this security mode, try --security ssl on port 465",
        ) from error
    except ssl.SSLError as error:
        raise transport_error(
            f"tls handshake failed: {error}",
            "check the port and security mode for this server",
        ) from error
    except (OSError, smtplib.SMTPServerDisconnected) as error:
        raise transport_error(
            f"cannot reach {entry.host}:{entry.port} ({error})",
            "check the host, the port, and whether a firewall blocks outbound submission",
            transient=True,
        ) from error
    finally:
        if client is not None:
            try:
                client.quit()
            except Exception:
                client.close()


def send_once(
    entry: profile,
    message: EmailMessage,
    recipients: list[str],
    timeout: float,
    notify=None,
) -> None:
    with connect(entry, timeout=timeout, notify=notify) as client:
        try:
            client.send_message(message, from_addr=entry.address, to_addrs=recipients)
        except smtplib.SMTPRecipientsRefused as error:
            refused = ", ".join(sorted(error.recipients))
            raise transport_error(f"every recipient was refused: {refused}") from error
        except smtplib.SMTPSenderRefused as error:
            raise transport_error(
                f"the server refused the sender {entry.address}",
                "an Aster token may only send from the address it is bound to",
            ) from error
        except smtplib.SMTPResponseException as error:
            raise transport_error(
                f"the server returned {error.smtp_code}: {error.smtp_error}",
                transient=error.smtp_code in TRANSIENT_STATUS_CODES,
            ) from error


def send(
    entry: profile,
    message: EmailMessage,
    recipients: list[str],
    timeout: float = DEFAULT_TIMEOUT,
    attempts: int = DEFAULT_ATTEMPTS,
    sleep=time.sleep,
    notify=None,
) -> int:
    if not entry.host:
        raise transport_error("no smtp host configured for this profile")

    total = max(1, attempts)
    last_error = None

    for attempt in range(1, total + 1):
        try:
            send_once(entry, message, recipients, timeout, notify=notify)

            return attempt
        except transport_error as error:
            last_error = error

            if not error.transient or attempt == total:
                raise

            delay = RETRY_BACKOFF_SECONDS * attempt

            if notify is not None:
                notify(f"{error.detail}, retrying in {delay:.0f}s ({attempt} of {total})")

            sleep(delay)

    raise last_error


def verify(entry: profile, timeout: float = DEFAULT_TIMEOUT, notify=None) -> str:
    with connect(entry, timeout=timeout, notify=notify) as client:
        return client.ehlo_resp.decode("utf-8", "replace") if client.ehlo_resp else ""
