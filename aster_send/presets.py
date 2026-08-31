# SPDX-License-Identifier: MIT
from dataclasses import dataclass

SECRET_PLACEHOLDER = "YOUR_TOKEN"


@dataclass(frozen=True)
class preset:
    key: str
    title: str
    template: str


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
    ),
)

PRESETS_BY_KEY = {item.key: item for item in PRESETS}


def render(key: str, values: dict) -> str:
    item = PRESETS_BY_KEY[key]

    return item.template.format(**values)


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
