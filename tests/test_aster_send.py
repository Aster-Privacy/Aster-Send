# SPDX-License-Identifier: MIT
import argparse
import io
import os
import re
import smtplib
import sys
import tempfile
import unittest
from email.message import EmailMessage
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aster_send import cli, config, message, presets, transport
from aster_send.token import parse_aster_token, redact

SAMPLE_TOKEN = "asmtp_abc234def567_not_a_real_secret"


def sample_profile(**overrides) -> config.profile:
    values = {
        "name": "default",
        "address": "bot@example.com",
        "secret": SAMPLE_TOKEN,
        "display_name": "Bot",
        "host": "mx.astermail.org",
        "port": 587,
        "security": "starttls",
        "username": "asmtp_abc234def567",
        "default_to": "me@example.com",
    }
    values.update(overrides)

    return config.profile(**values)


class token_tests(unittest.TestCase):
    def test_parses_selector_and_username(self):
        parsed = parse_aster_token(SAMPLE_TOKEN)

        self.assertEqual(parsed.selector, "abc234def567")
        self.assertEqual(parsed.username, "asmtp_abc234def567")
        self.assertEqual(parsed.host, "mx.astermail.org")
        self.assertEqual(parsed.port, 587)

    def test_rejects_other_passwords(self):
        for value in ["", "hunter2", "asmtp_", "asmtp_onlyselector", "asmtp_UPPER_secret"]:
            self.assertIsNone(parse_aster_token(value), value)

    def test_redaction_keeps_no_secret(self):
        redacted = redact(SAMPLE_TOKEN)

        self.assertNotIn("not_a_real_secret", redacted)
        self.assertIn("asmtp_abc234def567", redacted)

    def test_redaction_of_plain_password(self):
        self.assertNotIn("unter", redact("hunter2"))


class config_tests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "config.toml"

    def test_round_trip(self):
        settings = config.configuration(path=self.path, default_profile="default")
        settings.profiles["default"] = sample_profile(display_name='Bot "Quoted"')
        config.save(settings)

        reloaded = config.load(self.path)
        entry = reloaded.get()

        self.assertEqual(entry.address, "bot@example.com")
        self.assertEqual(entry.display_name, 'Bot "Quoted"')
        self.assertEqual(entry.secret, SAMPLE_TOKEN)
        self.assertEqual(entry.port, 587)

    def test_token_fills_missing_server_fields(self):
        entry = config.apply_token_defaults(
            config.profile(name="default", address="bot@example.com", secret=SAMPLE_TOKEN, port=0)
        )

        self.assertEqual(entry.host, "mx.astermail.org")
        self.assertEqual(entry.username, "asmtp_abc234def567")
        self.assertEqual(entry.port, 587)

    def test_unknown_profile_is_reported(self):
        settings = config.configuration(path=self.path)
        settings.profiles["default"] = sample_profile()

        with self.assertRaises(config.config_error):
            settings.get("missing")

    def test_empty_configuration_is_reported(self):
        with self.assertRaises(config.config_error):
            config.configuration(path=self.path).get()

    def test_config_directory_honors_override(self):
        with mock.patch.dict(os.environ, {"ASTER_SEND_CONFIG_DIR": self.directory.name}):
            self.assertEqual(config.config_directory(), Path(self.directory.name))


class message_tests(unittest.TestCase):
    def test_builds_headers(self):
        payload = message.build(
            sender="bot@example.com",
            recipients=["a@example.com"],
            subject="hello",
            body="text",
            display_name="Bot",
            cc=["c@example.com"],
        )

        self.assertEqual(payload["From"], "Bot <bot@example.com>")
        self.assertEqual(payload["To"], "a@example.com")
        self.assertEqual(payload["Cc"], "c@example.com")
        self.assertTrue(payload["Message-ID"].endswith("example.com>"))

    def test_rejects_invalid_addresses(self):
        with self.assertRaises(message.message_error):
            message.build("bot@example.com", ["nope"], "s", "b")

    def test_requires_a_recipient(self):
        with self.assertRaises(message.message_error):
            message.build("bot@example.com", [], "s", "b")

    def test_splits_and_deduplicates(self):
        self.assertEqual(
            message.split_recipients(["a@x.com, b@x.com; a@x.com", "c@x.com"]),
            ["a@x.com", "b@x.com", "c@x.com"],
        )

    def test_envelope_uses_bare_addresses(self):
        self.assertEqual(
            message.envelope_recipients(["Someone <a@x.com>"], ["b@x.com"], ["c@x.com"]),
            ["a@x.com", "b@x.com", "c@x.com"],
        )

    def test_html_alternative_is_multipart(self):
        payload = message.build(
            "bot@example.com", ["a@x.com"], "s", "plain", html="<p>rich</p>"
        )

        self.assertTrue(payload.is_multipart())

    def test_attachment_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "note.txt"
            path.write_text("hello", encoding="utf-8")
            payload = message.build("bot@example.com", ["a@x.com"], "s", "b", attachments=[path])
            names = [part.get_filename() for part in payload.iter_attachments()]

        self.assertIn("note.txt", names)

    def test_missing_attachment_is_reported(self):
        with self.assertRaises(message.message_error):
            message.build(
                "bot@example.com", ["a@x.com"], "s", "b", attachments=[Path("no_such_file.txt")]
            )

    def test_oversized_attachment_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "big.bin"
            path.write_bytes(b"0" * (message.MAX_ATTACHMENT_BYTES + 1))

            with self.assertRaises(message.message_error):
                message.build("bot@example.com", ["a@x.com"], "s", "b", attachments=[path])


class fake_smtp:
    instances = []

    def __init__(self, host, port, timeout=None, context=None, local_hostname=None):
        self.host = host
        self.port = port
        self.logged_in = None
        self.started_tls = False
        self.sent = []
        self.ehlo_resp = b"mx.astermail.org"
        self.quit_called = False
        fake_smtp.instances.append(self)

    def ehlo(self):
        return 250, b"ok"

    def starttls(self, context=None):
        self.started_tls = True

    def login(self, username, password):
        self.logged_in = (username, password)

    def send_message(self, message, from_addr=None, to_addrs=None):
        self.sent.append((from_addr, to_addrs, message))

    def quit(self):
        self.quit_called = True

    def close(self):
        self.quit_called = True


class transport_tests(unittest.TestCase):
    def setUp(self):
        fake_smtp.instances = []

    def test_starttls_login_and_send(self):
        entry = sample_profile()
        payload = message.build(entry.address, ["a@x.com"], "s", "b")

        with mock.patch.object(smtplib, "SMTP", fake_smtp):
            attempts = transport.send(entry, payload, ["a@x.com"])

        client = fake_smtp.instances[0]

        self.assertEqual(attempts, 1)
        self.assertTrue(client.started_tls)
        self.assertEqual(client.logged_in, ("asmtp_abc234def567", SAMPLE_TOKEN))
        self.assertEqual(client.sent[0][0], entry.address)
        self.assertTrue(client.quit_called)

    def test_authentication_error_points_at_a_new_token(self):
        entry = sample_profile()
        payload = message.build(entry.address, ["a@x.com"], "s", "b")

        def raise_auth(self, username, password):
            raise smtplib.SMTPAuthenticationError(535, b"bad credentials")

        with mock.patch.object(smtplib, "SMTP", fake_smtp):
            with mock.patch.object(fake_smtp, "login", raise_auth):
                with self.assertRaises(transport.transport_error) as caught:
                    transport.send(entry, payload, ["a@x.com"], attempts=1)

        self.assertIn("aster-send setup", caught.exception.hint)

    def test_transient_failure_is_retried(self):
        entry = sample_profile()
        payload = message.build(entry.address, ["a@x.com"], "s", "b")
        calls = {"count": 0}

        def flaky(self, message, from_addr=None, to_addrs=None):
            calls["count"] += 1

            if calls["count"] < 3:
                raise smtplib.SMTPResponseException(451, b"try later")

            self.sent.append((from_addr, to_addrs, message))

        with mock.patch.object(smtplib, "SMTP", fake_smtp):
            with mock.patch.object(fake_smtp, "send_message", flaky):
                attempts = transport.send(
                    entry, payload, ["a@x.com"], attempts=3, sleep=lambda seconds: None
                )

        self.assertEqual(attempts, 3)

    def test_permanent_failure_is_not_retried(self):
        entry = sample_profile()
        payload = message.build(entry.address, ["a@x.com"], "s", "b")
        calls = {"count": 0}

        def refused(self, message, from_addr=None, to_addrs=None):
            calls["count"] += 1
            raise smtplib.SMTPResponseException(550, b"rejected")

        with mock.patch.object(smtplib, "SMTP", fake_smtp):
            with mock.patch.object(fake_smtp, "send_message", refused):
                with self.assertRaises(transport.transport_error):
                    transport.send(
                        entry, payload, ["a@x.com"], attempts=3, sleep=lambda seconds: None
                    )

        self.assertEqual(calls["count"], 1)

    def test_sender_refusal_mentions_bound_address(self):
        entry = sample_profile()
        payload = message.build(entry.address, ["a@x.com"], "s", "b")

        def refused(self, message, from_addr=None, to_addrs=None):
            raise smtplib.SMTPSenderRefused(553, b"not allowed", entry.address)

        with mock.patch.object(smtplib, "SMTP", fake_smtp):
            with mock.patch.object(fake_smtp, "send_message", refused):
                with self.assertRaises(transport.transport_error) as caught:
                    transport.send(entry, payload, ["a@x.com"], attempts=1)

        self.assertIn("bound to", caught.exception.hint)

    def test_missing_host_is_reported(self):
        entry = sample_profile(host="")
        payload = message.build(entry.address, ["a@x.com"], "s", "b")

        with self.assertRaises(transport.transport_error):
            transport.send(entry, payload, ["a@x.com"])


class presets_tests(unittest.TestCase):
    def test_every_preset_renders(self):
        values = presets.build_values(
            "mx.astermail.org", 587, "starttls", "asmtp_abc234def567", "bot@x.com", "me@x.com", ""
        )

        unresolved = re.compile(r"(?<!\$)\{[a-z_]+\}")

        for item in presets.PRESETS:
            rendered = presets.render(item.key, values)

            self.assertIn("mx.astermail.org", rendered)
            self.assertIsNone(unresolved.search(rendered), item.key)

    def test_secret_is_a_placeholder_by_default(self):
        values = presets.build_values(
            "mx.astermail.org", 587, "starttls", "asmtp_abc234def567", "bot@x.com", "me@x.com", ""
        )

        self.assertEqual(values["secret"], presets.SECRET_PLACEHOLDER)

    def test_ssl_changes_the_labels(self):
        values = presets.build_values(
            "mail.example.com", 465, "ssl", "bot@x.com", "bot@x.com", "me@x.com", "pw"
        )

        self.assertEqual(values["security_label"], "SSL/TLS")
        self.assertEqual(values["use_ssl_python"], "True")
        self.assertEqual(values["use_tls_python"], "False")


class cli_tests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        patcher = mock.patch.dict(os.environ, {"ASTER_SEND_CONFIG_DIR": self.directory.name})
        patcher.start()
        self.addCleanup(patcher.stop)

        settings = config.configuration(path=config.config_path(), default_profile="default")
        settings.profiles["default"] = sample_profile()
        config.save(settings)

        fake_smtp.instances = []

    def run_cli(self, arguments, stdin_text=""):
        stdout = io.StringIO()
        stderr = io.StringIO()

        with mock.patch.object(sys, "stdout", stdout):
            with mock.patch.object(sys, "stderr", stderr):
                with mock.patch.object(sys, "stdin", io.StringIO(stdin_text)):
                    with mock.patch.object(smtplib, "SMTP", fake_smtp):
                        code = cli.main(arguments)

        return code, stdout.getvalue(), stderr.getvalue()

    def test_bare_recipient_is_treated_as_send(self):
        self.assertEqual(cli.normalize(["a@x.com", "-s", "hi"]), ["send", "a@x.com", "-s", "hi"])

    def test_known_command_is_left_alone(self):
        self.assertEqual(cli.normalize(["doctor"]), ["doctor"])

    def test_profile_flag_before_recipient(self):
        self.assertEqual(
            cli.normalize(["-p", "work", "a@x.com"]), ["-p", "work", "send", "a@x.com"]
        )

    def test_leading_flag_is_treated_as_send(self):
        self.assertEqual(cli.normalize(["-s", "hi"]), ["send", "-s", "hi"])

    def test_send_from_stdin(self):
        code, stdout, stderr = self.run_cli(["a@x.com", "-s", "hello"], stdin_text="body text")

        self.assertEqual(code, 0, stderr)
        self.assertIn("Sent to a@x.com", stdout)

        sent = fake_smtp.instances[0].sent[0][2]

        self.assertEqual(sent["Subject"], "hello")
        self.assertIn("body text", sent.get_content())

    def test_send_falls_back_to_default_recipient(self):
        code, stdout, stderr = self.run_cli(["-s", "hello", "-m", "body"])

        self.assertEqual(code, 0, stderr)
        self.assertIn("me@example.com", stdout)

    def test_dry_run_sends_nothing(self):
        code, stdout, stderr = self.run_cli(["a@x.com", "-s", "hi", "-m", "b", "--dry-run"])

        self.assertEqual(code, 0, stderr)
        self.assertIn("[dry run]", stdout)
        self.assertEqual(fake_smtp.instances, [])

    def test_invalid_recipient_exits_with_one(self):
        code, stdout, stderr = self.run_cli(["nope", "-s", "hi", "-m", "b"])

        self.assertEqual(code, 1)
        self.assertIn("invalid email address", stderr)

    def test_run_mails_only_on_failure(self):
        code, stdout, stderr = self.run_cli(
            ["run", "--", sys.executable, "-c", "print('fine')"]
        )

        self.assertEqual(code, 0)
        self.assertIn("fine", stdout)
        self.assertEqual(fake_smtp.instances, [])

    def test_run_mails_when_the_command_fails(self):
        code, stdout, stderr = self.run_cli(
            ["run", "--", sys.executable, "-c", "import sys; sys.stderr.write('boom'); sys.exit(3)"]
        )

        self.assertEqual(code, 3)

        sent = fake_smtp.instances[0].sent[0][2]

        self.assertIn("failed with exit code 3", sent["Subject"])
        self.assertIn("boom", sent.get_content())

    def test_run_always_mails_on_success(self):
        code, stdout, stderr = self.run_cli(
            ["run", "--always", "--", sys.executable, "-c", "print('ok')"]
        )

        self.assertEqual(code, 0)
        self.assertIn("finished", fake_smtp.instances[0].sent[0][2]["Subject"])

    def test_config_hides_the_secret_by_default(self):
        code, stdout, stderr = self.run_cli(["config", "django"])

        self.assertEqual(code, 0, stderr)
        self.assertIn(presets.SECRET_PLACEHOLDER, stdout)
        self.assertNotIn(SAMPLE_TOKEN, stdout)

    def test_config_reveal_prints_the_secret(self):
        code, stdout, stderr = self.run_cli(["config", "django", "--reveal"])

        self.assertEqual(code, 0, stderr)
        self.assertIn(SAMPLE_TOKEN, stdout)

    def test_config_list(self):
        code, stdout, stderr = self.run_cli(["config"])

        self.assertEqual(code, 0, stderr)
        self.assertIn("home-assistant", stdout)

    def test_unknown_config_is_reported(self):
        code, stdout, stderr = self.run_cli(["config", "nothing-here"])

        self.assertEqual(code, 1)
        self.assertIn("unknown configuration", stderr)

    def test_doctor_redacts_the_password(self):
        code, stdout, stderr = self.run_cli(["doctor"])

        self.assertEqual(code, 0, stderr)
        self.assertNotIn(SAMPLE_TOKEN, stdout)
        self.assertIn("Connected and authenticated", stdout)

    def test_profiles_marks_the_default(self):
        code, stdout, stderr = self.run_cli(["profiles"])

        self.assertEqual(code, 0, stderr)
        self.assertIn("* default", stdout)

    def test_no_arguments_prints_help(self):
        code, stdout, stderr = self.run_cli([])

        self.assertEqual(code, 2)
        self.assertIn("usage:", stdout)

class hardening_tests(unittest.TestCase):
    def test_selector_alphabet_is_base32(self):
        self.assertIsNotNone(parse_aster_token("asmtp_234567234567_secret"))
        self.assertIsNone(parse_aster_token("asmtp_ａｂｃ_secret"))
        self.assertIsNone(parse_aster_token("asmtp_abc189_secret"))
        self.assertIsNone(parse_aster_token("asmtp_abc234_secret with spaces"))

    def test_config_escapes_control_characters(self):
        messy_name = "Bot\nX-Injected: 1"
        messy_secret = 'pass"word\ttab\\slash'

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.toml"
            settings = config.configuration(path=path, default_profile="default")
            settings.profiles["default"] = sample_profile(
                display_name=messy_name, secret=messy_secret
            )
            config.save(settings)
            reloaded = config.load(path)

        self.assertEqual(reloaded.profiles["default"].display_name, messy_name)
        self.assertEqual(reloaded.profiles["default"].secret, messy_secret)

    def test_config_file_is_owner_only(self):
        if os.name == "nt":
            self.skipTest("posix permissions only")

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.toml"
            settings = config.configuration(path=path, default_profile="default")
            settings.profiles["default"] = sample_profile()
            config.save(settings)

            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_malformed_config_raises_config_error(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.toml"
            path.write_text("this is not toml = = =", encoding="utf-8")

            with self.assertRaises(config.config_error):
                config.load(path)

    def test_invalid_port_raises_config_error(self):
        body = '[profiles.default]\naddress = "a@b.com"\nport = "not-a-port"\n'

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.toml"
            path.write_text(body, encoding="utf-8")

            with self.assertRaises(config.config_error):
                config.load(path)

    def test_out_of_range_port_raises_config_error(self):
        body = '[profiles.default]\naddress = "a@b.com"\nport = 99999\n'

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.toml"
            path.write_text(body, encoding="utf-8")

            with self.assertRaises(config.config_error):
                config.load(path)

    def test_profile_names_are_validated(self):
        with self.assertRaises(config.config_error):
            config.validate_profile_name("has space")

        with self.assertRaises(config.config_error):
            config.validate_profile_name("")

        self.assertEqual(config.validate_profile_name(" work "), "work")

    def test_profile_name_needing_quotes_round_trips(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.toml"
            settings = config.configuration(path=path, default_profile="odd.name")
            settings.profiles["odd.name"] = sample_profile(name="odd.name")
            config.save(settings)
            reloaded = config.load(path)

        self.assertIn("odd.name", reloaded.profiles)

    def test_plaintext_auth_is_refused_by_default(self):
        entry = sample_profile(security="none")

        with self.assertRaises(transport.transport_error) as caught:
            with transport.connect(entry):
                pass

        self.assertIn("unencrypted", caught.exception.detail)

    def test_plaintext_auth_can_be_allowed(self):
        entry = sample_profile(security="none", allow_plaintext_auth=True)
        client = fake_smtp("mx.astermail.org", 587)

        with mock.patch.object(transport.smtplib, "SMTP", return_value=client):
            with transport.connect(entry) as opened:
                self.assertIs(opened, client)

    def test_unknown_security_mode_is_rejected(self):
        entry = sample_profile(security="banana")

        with self.assertRaises(transport.transport_error):
            with transport.connect(entry):
                pass

    def test_addresses_with_whitespace_are_rejected(self):
        for value in ["a b@example.com", "a@ex ample.com", "a@example", "a@@example.com"]:
            with self.assertRaises(message.message_error, msg=value):
                message.validate_address(value)

    def test_header_injection_is_reported_cleanly(self):
        subject = "Hi\r\nBcc: evil@example.com"

        with self.assertRaises(ValueError):
            message.build("a@example.com", ["b@example.com"], subject, "body")

    def test_example_command_matches_the_platform(self):
        example = cli.example_command("me@example.com")

        if os.name == "nt":
            self.assertNotIn("'", example)
        else:
            self.assertIn("echo", example)

    def test_run_subject_uses_the_program_name(self):
        self.assertEqual(cli.command_label(["/usr/local/bin/backup.sh", "-v"]), "backup.sh")
        self.assertEqual(cli.command_label(["/opt/bin/rsync.EXE"]), "rsync")
        self.assertEqual(cli.command_label(["python", "-c", "print(1)"]), "python")

    def test_secret_prompt_is_hidden_on_a_terminal(self):
        with mock.patch.object(cli.sys, "stdin") as fake_stdin:
            fake_stdin.isatty.return_value = True

            with mock.patch.object(cli.getpass, "getpass", return_value=" token ") as hidden:
                self.assertEqual(cli.prompt_secret("Password: "), "token")

            hidden.assert_called_once()

    def test_secret_prompt_falls_back_without_a_terminal(self):
        with mock.patch.object(cli.sys, "stdin") as fake_stdin:
            fake_stdin.isatty.return_value = False

            with mock.patch.object(cli, "input", create=True, return_value="piped"):
                self.assertEqual(cli.prompt_secret("Password: "), "piped")

    def test_plaintext_flag_round_trips_through_the_config_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.toml"
            settings = config.configuration(path=path, default_profile="default")
            settings.profiles["default"] = sample_profile(allow_plaintext_auth=True)
            config.save(settings)

            self.assertIn("allow_plaintext_auth = true", path.read_text(encoding="utf-8"))
            self.assertTrue(config.load(path).profiles["default"].allow_plaintext_auth)

    def test_plaintext_flag_defaults_to_false(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.toml"
            path.write_text('[profiles.default]\naddress = "a@b.com"\n', encoding="utf-8")

            self.assertFalse(config.load(path).profiles["default"].allow_plaintext_auth)

    def test_non_boolean_plaintext_flag_is_rejected(self):
        body = '[profiles.default]\naddress = "a@b.com"\nallow_plaintext_auth = "yes"\n'

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.toml"
            path.write_text(body, encoding="utf-8")

            with self.assertRaises(config.config_error):
                config.load(path)

    def test_a_folder_is_not_a_valid_attachment(self):
        with tempfile.TemporaryDirectory() as folder:
            payload = EmailMessage()

            with self.assertRaises(message.message_error) as caught:
                message.attach_file(payload, Path(folder))

            self.assertIn("folder", str(caught.exception))

    def test_numeric_options_reject_nonsense(self):
        for value in ["-5", "0", "abc"]:
            with self.assertRaises(argparse.ArgumentTypeError, msg=value):
                cli.positive_seconds(value)

        for value in ["-1", "0", "1.5", "abc"]:
            with self.assertRaises(argparse.ArgumentTypeError, msg=value):
                cli.positive_count(value)

        self.assertEqual(cli.positive_seconds("2.5"), 2.5)
        self.assertEqual(cli.positive_count("3"), 3)

    def test_help_shows_the_default_send_form(self):
        text = cli.build_parser().format_help()

        self.assertIn("aster-send you@example.com", text)
        self.assertIn("aster-send setup", text)

    def test_no_password_is_sent_when_starttls_is_missing(self):
        entry = sample_profile(security="starttls")
        client = fake_smtp("mx.astermail.org", 587)
        client.starttls = mock.Mock(
            side_effect=smtplib.SMTPNotSupportedError("STARTTLS extension not supported")
        )
        client.login = mock.Mock()

        with mock.patch.object(transport.smtplib, "SMTP", return_value=client):
            with self.assertRaises(transport.transport_error):
                with transport.connect(entry):
                    pass

        client.login.assert_not_called()

    def test_local_hostname_is_a_valid_ehlo_domain(self):
        name = transport.local_hostname()

        self.assertTrue(name)
        self.assertNotIn(" ", name)

        if name.startswith("["):
            self.assertTrue(name.endswith("]"))
        else:
            self.assertIn(".", name)


if __name__ == "__main__":
    unittest.main()
