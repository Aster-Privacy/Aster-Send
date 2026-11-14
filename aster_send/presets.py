# SPDX-License-Identifier: MIT
import json
import re
from dataclasses import dataclass

SECRET_PLACEHOLDER = "YOUR_TOKEN"
QUOTED_FIELDS = ("host", "username", "address", "local_part", "domain", "recipient", "secret")
CONTROL_CHARACTER = re.compile(r"[\x00-\x1f\x7f\x85\u2028\u2029]")
BARE_VALUE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._@+/=-]*")
YAML_KEYWORDS = frozenset({"true", "false", "yes", "no", "on", "off", "null", "y", "n"})


class preset_error(ValueError):
    pass


@dataclass(frozen=True)
class preset:
    key: str
    title: str
    template: str
    syntax: str = "plain"


def reads_as_a_number(value: str) -> bool:
    for parse in (float, lambda text: int(text, 0)):
        try:
            parse(value)
        except ValueError:
            continue

        return True

    return False


def is_bare(value: str) -> bool:
    return not value or BARE_VALUE.fullmatch(value) is not None


def quote_plain(value: str) -> str:
    return value


def quote_yaml(value: str) -> str:
    if is_bare(value) and value.lower() not in YAML_KEYWORDS and not reads_as_a_number(value):
        return value

    return json.dumps(value)


def quote_php(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def quote_python(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)[1:-1]


def quote_ruby(value: str) -> str:
    return quote_python(value).replace("#", "\\#")


def quote_ini(value: str) -> str:
    if is_bare(value):
        return value

    if '"""' not in value:
        return f'"""{value}"""'

    if "`" not in value:
        return f"`{value}`"

    raise preset_error("this value cannot be written safely in an INI file")


def quote_env(value: str) -> str:
    if is_bare(value):
        return value

    if "'" not in value:
        return f"'{value}'"

    escaped = value

    for character in ("\\", '"', "$", "`"):
        escaped = escaped.replace(character, f"\\{character}")

    return f'"{escaped}"'


def quote_msmtp(value: str) -> str:
    if is_bare(value):
        return value

    if '"' not in value:
        return f'"{value}"'

    raise preset_error("msmtp cannot read a value that contains a double quote")


QUOTERS = {
    "plain": quote_plain,
    "yaml": quote_yaml,
    "php": quote_php,
    "python": quote_python,
    "ruby": quote_ruby,
    "ini": quote_ini,
    "env": quote_env,
    "msmtp": quote_msmtp,
}


PRESETS: tuple[preset, ...] = (
    preset(
        "home-assistant",
        "Home Assistant (configuration.yaml)",
        """notify:
  - name: email
    platform: smtp
    server: {host}
    port: {port}
    encryption: {starttls_yes_no}
    username: {username}
    password: {secret}
    sender: {address}
    recipient:
      - {recipient}""",
        "yaml",
    ),
    preset(
        "gitea",
        "Gitea (app.ini)",
        """[mailer]
ENABLED = true
PROTOCOL = {protocol}
SMTP_ADDR = {host}
SMTP_PORT = {port}
FROM = {address}
USER = {username}
PASSWD = {secret}""",
        "ini",
    ),
    preset(
        "grafana",
        "Grafana (grafana.ini)",
        """[smtp]
enabled = true
host = {host}:{port}
user = {username}
password = {secret}
from_address = {address}
from_name = Grafana
startTLS_policy = {starttls_policy}""",
        "ini",
    ),
    preset(
        "nextcloud",
        "Nextcloud (config.php)",
        """'mail_smtpmode' => 'smtp',
'mail_smtphost' => '{host}',
'mail_smtpport' => {port},
'mail_smtpsecure' => '{secure_mode}',
'mail_smtpauth' => true,
'mail_smtpname' => '{username}',
'mail_smtppassword' => '{secret}',
'mail_from_address' => '{local_part}',
'mail_domain' => '{domain}',""",
        "php",
    ),
    preset(
        "uptime-kuma",
        "Uptime Kuma (notification settings)",
        """Notification type   Email (SMTP)
Hostname            {host}
Port                {port}
Security            {security_label}
Username            {username}
Password            {secret}
From email          {address}
To email            {recipient}""",
        "plain",
    ),
    preset(
        "django",
        "Django (settings.py)",
        """EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = "{host}"
EMAIL_PORT = {port}
EMAIL_USE_TLS = {use_tls_python}
EMAIL_USE_SSL = {use_ssl_python}
EMAIL_HOST_USER = "{username}"
EMAIL_HOST_PASSWORD = "{secret}"
DEFAULT_FROM_EMAIL = "{address}\"""",
        "python",
    ),
    preset(
        "rails",
        "Rails (config/environments/production.rb)",
        """config.action_mailer.delivery_method = :smtp
config.action_mailer.smtp_settings = {{
  address: "{host}",
  port: {port},
  user_name: "{username}",
  password: "{secret}",
  authentication: :plain,
  enable_starttls_auto: {starttls_ruby}
}}""",
        "ruby",
    ),
    preset(
        "wordpress",
        "WordPress (wp-config.php with an SMTP plugin)",
        """define('SMTP_HOST', '{host}');
define('SMTP_PORT', {port});
define('SMTP_SECURE', '{secure_mode}');
define('SMTP_AUTH', true);
define('SMTP_USER', '{username}');
define('SMTP_PASS', '{secret}');
define('SMTP_FROM', '{address}');""",
        "php",
    ),
    preset(
        "github-actions",
        "GitHub Actions (workflow step)",
        """- name: Send mail
  uses: dawidd6/action-send-mail@v3
  with:
    server_address: {host}
    server_port: {port}
    secure: {secure_bool}
    username: ${{{{ secrets.SMTP_USERNAME }}}}
    password: ${{{{ secrets.SMTP_PASSWORD }}}}
    from: {address}
    to: {recipient}
    subject: Build ${{{{ github.run_number }}}}
    body: The workflow finished.""",
        "yaml",
    ),
    preset(
        "msmtp",
        "msmtp (~/.msmtprc, a sendmail replacement for cron)",
        """defaults
auth           on
tls            on
tls_starttls   {starttls_on_off}
logfile        ~/.msmtp.log

account        aster
host           {host}
port           {port}
from           {address}
user           {username}
password       {secret}

account default : aster""",
        "msmtp",
    ),
    preset(
        "docker-compose",
        "Docker Compose (environment block)",
        """environment:
  SMTP_HOST: {host}
  SMTP_PORT: "{port}"
  SMTP_SECURITY: {security_label}
  SMTP_USERNAME: {username}
  SMTP_PASSWORD: ${{SMTP_PASSWORD}}
  SMTP_FROM: {address}""",
        "yaml",
    ),
    preset(
        "env",
        "Environment variables (.env)",
        """SMTP_HOST={host}
SMTP_PORT={port}
SMTP_SECURITY={security_label}
SMTP_USERNAME={username}
SMTP_PASSWORD={secret}
SMTP_FROM={address}""",
        "env",
    ),
)

PRESETS_BY_KEY = {item.key: item for item in PRESETS}


def render(key: str, values: dict) -> str:
    item = PRESETS_BY_KEY[key]
    quote = QUOTERS[item.syntax]
    quoted = dict(values)

    for name in QUOTED_FIELDS:
        value = str(values[name])

        if CONTROL_CHARACTER.search(value):
            raise preset_error(
                f"the {name.replace('_', ' ')} contains a line break or control character, "
                "so it cannot be written to a configuration file"
            )

        quoted[name] = quote(value)

    return item.template.format(**quoted)


def build_values(
    host: str,
    port: int,
    security: str,
    username: str,
    address: str,
    recipient: str,
    secret: str,
) -> dict:
    normalized = (security or "starttls").lower()
    is_starttls = normalized == "starttls"
    is_ssl = normalized in {"ssl", "tls", "smtps"}
    local_part, _, domain = address.partition("@")

    return {
        "host": host,
        "port": port,
        "username": username,
        "address": address,
        "local_part": local_part,
        "domain": domain,
        "recipient": recipient or address,
        "secret": secret or SECRET_PLACEHOLDER,
        "security_label": "SSL/TLS" if is_ssl else ("STARTTLS" if is_starttls else "None"),
        "secure_mode": "ssl" if is_ssl else ("tls" if is_starttls else ""),
        "protocol": "smtps" if is_ssl else "smtp+starttls",
        "starttls_yes_no": "starttls" if is_starttls else ("tls" if is_ssl else "none"),
        "starttls_policy": "MandatoryStartTLS" if is_starttls else "NoStartTLS",
        "starttls_on_off": "on" if is_starttls else "off",
        "starttls_ruby": "true" if is_starttls else "false",
        "use_tls_python": "True" if is_starttls else "False",
        "use_ssl_python": "True" if is_ssl else "False",
        "secure_bool": "true" if is_ssl else "false",
    }
