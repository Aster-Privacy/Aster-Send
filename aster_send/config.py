# SPDX-License-Identifier: MIT
import os
import stat
import subprocess
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .token import parse_aster_token

KEYRING_SERVICE = "aster-send"
CONFIG_FILE_NAME = "config.toml"


class config_error(Exception):
    pass


@dataclass
class profile:
    name: str
    address: str
    secret: str = ""
    display_name: str = ""
    host: str = ""
    port: int = 587
    security: str = "starttls"
    username: str = ""
    default_to: str = ""
    allow_plaintext_auth: bool = False

    def resolved_username(self) -> str:
        return self.username or self.address

    def stored_fields(self) -> dict:
        fields = {
            "address": self.address,
            "host": self.host,
            "port": self.port,
            "security": self.security,
            "username": self.username,
        }

        if self.display_name:
            fields["display_name"] = self.display_name

        if self.default_to:
            fields["default_to"] = self.default_to

        if self.allow_plaintext_auth:
            fields["allow_plaintext_auth"] = True

        return fields


@dataclass
class configuration:
    path: Path
    default_profile: str = ""
    profiles: dict[str, profile] = field(default_factory=dict)

    def get(self, name: str = "") -> profile:
        if not self.profiles:
            raise config_error("no profiles configured, run 'aster-send setup' first")

        wanted = name or self.default_profile or next(iter(self.profiles))

        if wanted not in self.profiles:
            known = ", ".join(sorted(self.profiles)) or "none"
            raise config_error(f"unknown profile '{wanted}', configured profiles: {known}")

        return self.profiles[wanted]


def config_directory() -> Path:
    override = os.environ.get("ASTER_SEND_CONFIG_DIR")

    if override:
        return Path(override)

    if os.name == "nt":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")

        return Path(base) / "aster-send"

    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")

    return Path(base) / "aster-send"


def config_path() -> Path:
    return config_directory() / CONFIG_FILE_NAME


def apply_token_defaults(entry: profile) -> profile:
    parsed = parse_aster_token(entry.secret)

    if parsed is None:
        return entry

    entry.host = entry.host or parsed.host
    entry.username = entry.username or parsed.username
    entry.security = entry.security or parsed.security

    if not entry.port:
        entry.port = parsed.port

    return entry


def read_port(target: Path, name: str, value) -> int:
    if value is None or value == "":
        return 587

    try:
        port = int(value)
    except (TypeError, ValueError):
        raise config_error(f"profile '{name}' in {target} has an invalid port: {value!r}") from None

    if not 1 <= port <= 65535:
        raise config_error(f"profile '{name}' in {target} has a port outside 1 to 65535: {port}")

    return port


def read_flag(target: Path, name: str, key: str, value) -> bool:
    if value is None:
        return False

    if not isinstance(value, bool):
        raise config_error(f"profile '{name}' in {target} needs true or false for {key}")

    return value


def load(path: Path | None = None) -> configuration:
    target = path or config_path()
    result = configuration(path=target)

    if not target.exists():
        return result

    try:
        with target.open("rb") as handle:
            raw = tomllib.load(handle)
    except OSError as error:
        raise config_error(f"cannot read {target}: {error}") from error
    except tomllib.TOMLDecodeError as error:
        raise config_error(f"{target} is not valid TOML: {error}") from error

    if not isinstance(raw.get("profiles", {}), dict):
        raise config_error(f"{target} has a malformed profiles section")

    result.default_profile = str(raw.get("default_profile", ""))

    for name, values in (raw.get("profiles") or {}).items():
        if not isinstance(values, dict):
            continue

        entry = profile(
            name=name,
            address=str(values.get("address", "")),
            secret=str(values.get("password", "")),
            display_name=str(values.get("display_name", "")),
            host=str(values.get("host", "")),
            port=read_port(target, name, values.get("port")),
            security=str(values.get("security", "starttls")),
            username=str(values.get("username", "")),
            default_to=str(values.get("default_to", "")),
            allow_plaintext_auth=read_flag(
                target, name, "allow_plaintext_auth", values.get("allow_plaintext_auth")
            ),
        )

        if not entry.secret:
            entry.secret = read_keyring_secret(name)

        result.profiles[name] = apply_token_defaults(entry)

    return result


def read_keyring_secret(name: str) -> str:
    try:
        import keyring
    except ImportError:
        return ""

    try:
        return keyring.get_password(KEYRING_SERVICE, name) or ""
    except Exception:
        return ""


def write_keyring_secret(name: str, secret: str) -> bool:
    try:
        import keyring
    except ImportError:
        return False

    try:
        keyring.set_password(KEYRING_SERVICE, name, secret)
    except Exception:
        return False

    return True


def delete_keyring_secret(name: str) -> None:
    try:
        import keyring
    except ImportError:
        return

    try:
        keyring.delete_password(KEYRING_SERVICE, name)
    except Exception:
        return


TOML_ESCAPES = {
    "\\": "\\\\",
    '"': '\\"',
    "\b": "\\b",
    "\t": "\\t",
    "\n": "\\n",
    "\f": "\\f",
    "\r": "\\r",
}
BARE_KEY_ALPHABET = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
)


def quote(value: str) -> str:
    pieces = []

    for character in value:
        if character in TOML_ESCAPES:
            pieces.append(TOML_ESCAPES[character])
        elif character < " " or character == "\x7f":
            pieces.append(f"\\u{ord(character):04X}")
        else:
            pieces.append(character)

    return '"{}"'.format("".join(pieces))


def quote_key(name: str) -> str:
    if name and BARE_KEY_ALPHABET.issuperset(name):
        return name

    return quote(name)


def validate_profile_name(name: str) -> str:
    cleaned = name.strip()

    if not cleaned:
        raise config_error("a profile name is required")

    if not BARE_KEY_ALPHABET.issuperset(cleaned):
        raise config_error(
            f"invalid profile name '{name}', use letters, digits, hyphens, and underscores"
        )

    return cleaned


def render(settings: configuration) -> str:
    lines = []

    if settings.default_profile:
        lines.append(f"default_profile = {quote(settings.default_profile)}")
        lines.append("")

    for name in sorted(settings.profiles):
        entry = settings.profiles[name]
        lines.append(f"[profiles.{quote_key(name)}]")

        for key, value in entry.stored_fields().items():
            if value == "" or value is None:
                continue

            if isinstance(value, bool):
                rendered = "true" if value else "false"
            elif isinstance(value, int):
                rendered = str(value)
            else:
                rendered = quote(str(value))

            lines.append(f"{key} = {rendered}")

        if entry.secret:
            lines.append(f"password = {quote(entry.secret)}")

        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def restrict_permissions(path: Path) -> bool:
    if os.name != "nt":
        try:
            path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            return False

        return True

    account = os.environ.get("USERNAME", "").strip()

    if not account:
        return False

    try:
        completed = subprocess.run(
            ["icacls", str(path), "/inheritance:r", "/grant:r", f"{account}:F"],
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return False

    return completed.returncode == 0


def save(settings: configuration) -> Path:
    try:
        settings.path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise config_error(f"cannot create {settings.path.parent}: {error}") from error

    temporary = settings.path.with_name(f"{settings.path.name}.{os.getpid()}.tmp")
    descriptor = None

    try:
        descriptor = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, stat.S_IRUSR | stat.S_IWUSR
        )

        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            descriptor = None
            handle.write(render(settings))
            handle.flush()
            os.fsync(handle.fileno())

        os.replace(temporary, settings.path)
    except OSError as error:
        if descriptor is not None:
            os.close(descriptor)

        temporary.unlink(missing_ok=True)

        raise config_error(f"cannot write {settings.path}: {error}") from error

    restrict_permissions(settings.path)

    return settings.path
