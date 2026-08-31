# SPDX-License-Identifier: MIT
from dataclasses import dataclass

ASTER_TOKEN_PREFIX = "asmtp_"
ASTER_HOST = "mx.astermail.org"
ASTER_PORT = 587
ASTER_SECURITY = "starttls"
SELECTOR_ALPHABET = frozenset("abcdefghijklmnopqrstuvwxyz234567")
SECRET_ALPHABET = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
)


@dataclass(frozen=True)
class aster_token:
    selector: str
    username: str
    host: str = ASTER_HOST
    port: int = ASTER_PORT
    security: str = ASTER_SECURITY


def looks_like_aster_token(secret: str) -> bool:
    return parse_aster_token(secret) is not None


def parse_aster_token(secret: str) -> aster_token | None:
    if not secret or not secret.startswith(ASTER_TOKEN_PREFIX):
        return None

    remainder = secret[len(ASTER_TOKEN_PREFIX) :]
    selector, separator, token_secret = remainder.partition("_")

    if not separator or not selector or not token_secret:
        return None

    if not SELECTOR_ALPHABET.issuperset(selector):
        return None

    if not SECRET_ALPHABET.issuperset(token_secret):
        return None

    return aster_token(selector=selector, username=f"{ASTER_TOKEN_PREFIX}{selector}")


def redact(secret: str) -> str:
    parsed = parse_aster_token(secret)

    if parsed is not None:
        return f"{parsed.username}_{'*' * 8}"

    if len(secret) <= 4:
        return "*" * len(secret)

    return f"{secret[:2]}{'*' * (len(secret) - 4)}{secret[-2:]}"
