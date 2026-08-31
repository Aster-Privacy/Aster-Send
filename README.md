<img width="200" alt="Aster" src="https://raw.githubusercontent.com/Aster-Privacy/.github/main/profile/aster_logo.png" />

# aster-send

`aster-send` is a free, open-source command-line tool for sending email through any SMTP server. It has no dependencies outside the Python standard library, keeps its settings in a single file, and runs nothing in the background.

It is built around Aster's send-only SMTP tokens. A token sends only, never reads, and is locked to one of your verified custom-domain addresses, so a script that holds one cannot reach your inbox. Paste a token and `aster-send` configures itself, because the host, the port, and the username all come from the token.

You can sign up at [astermail.org](https://astermail.org). SMTP tokens require a Star plan or higher.

```
$ backup.sh | aster-send me@example.com -s "Nightly backup"
Sent to me@example.com
```

## How it works

An Aster token looks like `asmtp_<selector>_<secret>`. The selector is also the SMTP username, so a single value carries everything needed to connect. When you paste one, `aster-send` fills in `mx.astermail.org`, port 587, and STARTTLS for you. For any other provider, it asks for the host, the port, and the username instead, and works the same way from then on.

Connections use STARTTLS by default, certificates are verified, and the client refuses to send your password over an unencrypted connection. Passwords go to your system keychain when the `keyring` package is installed, and otherwise stay in a config file that only your user account can read.

> [!NOTE]
> Email sent through an SMTP token is protected by TLS while in transit and stored with zero-access encryption on our servers, but it is not end-to-end encrypted. Aster cannot apply end-to-end encryption to mail that originates outside the Aster apps. Only use SMTP tokens for automated or transactional mail where end-to-end encryption is not required.

## Getting started

You need Python 3.11 or later.

```bash
git clone https://github.com/Aster-Privacy/Aster-Send.git
cd Aster-Send
pip install .
```

To store passwords in your system keychain rather than the config file, install the optional extra:

```bash
pip install ".[keyring]"
```

If `aster-send` is not on your `PATH` after installing, run it as `python -m aster_send` instead.

To create a token, open **Settings** > **Bridge** in Aster Mail, then choose **Generate token**. The password appears once and cannot be retrieved again. Then run:

```bash
aster-send setup
```

Paste the token when the command asks for a password. It recognizes the format, fills in the server settings, and asks only for the address you send from.

## Sending mail

Pass recipients, a subject, and a body. Without `-m`, the body comes from standard input:

```bash
aster-send you@example.com -s "Deploy finished" -m "Version 2.1 is live."

df -h | aster-send you@example.com -s "Disk usage"

aster-send you@example.com -s "Report" -m "Attached." -a report.pdf
```

If you set a default recipient during setup, you can leave the address out:

```bash
echo "the job is done" | aster-send -s "Nightly job"
```

| Option | What it does |
|---|---|
| `-s`, `--subject` | Sets the subject |
| `-m`, `--body` | Sets the body, otherwise the body comes from standard input |
| `-a`, `--attach` | Attaches a file, repeat for several |
| `--html` | Adds an HTML alternative body from a file |
| `--cc`, `--bcc` | Adds copy recipients |
| `--reply-to` | Sets a different reply address |
| `--dry-run` | Prints the message instead of sending it |
| `-p`, `--profile` | Sends through another profile |
| `--allow-plaintext-auth` | Permits authentication on an unencrypted connection |
| `--timeout`, `--attempts` | Sets the connection timeout in seconds and how many tries to make |
| `-q`, `--quiet` | Prints nothing on success |

## Mailing yourself when a command fails

`aster-send run` runs a command and mails you the output only when that command fails. Left in a cron job, it tells you about the job on the day it breaks:

```bash
aster-send run -- rsync -a /data /backup

0 3 * * * aster-send run --to me@example.com -- /usr/local/bin/backup.sh
```

The first form mails your default recipient, so set one during setup or pass `--to`. To get a message every time, add `--always`. Your command's exit code becomes the exit code of `aster-send`, so nothing downstream changes.

## Configuring other apps

Most apps want the same SMTP settings in a slightly different shape. To print the block for one of them, name it:

```bash
aster-send config gitea
aster-send config home-assistant
```

The output carries a `YOUR_TOKEN` placeholder rather than your real password. To print the password as well, add `--reveal`. For the full list, run `aster-send config --list`. Home Assistant, Gitea, Grafana, Nextcloud, Uptime Kuma, Django, Rails, WordPress, GitHub Actions, msmtp, Docker Compose, and plain environment variables are covered so far.

## Checking your setup

```bash
aster-send doctor
```

The command prints the profile it resolved, connects, authenticates, and reports what went wrong if anything did. It redacts your password, so the output is safe to paste into a bug report.

## Profiles

A profile is an address and the server that sends for it. To keep several, name them during setup and choose one per message:

```bash
aster-send -p alerts you@example.com -s "Disk full"
aster-send profiles
```

Settings live in `config.toml`, under `%APPDATA%\aster-send` on Windows and `~/.config/aster-send` everywhere else. To move it, set `ASTER_SEND_CONFIG_DIR`.

## Token rules

- Tokens require a Star plan or higher, and each one binds to a verified custom-domain address. Addresses at `astermail.org` cannot hold a token.
- One active token exists per address. To rotate a token, revoke the old one first, then create its replacement.
- Tokens send only. They cannot read mail, list folders, or reach the rest of your account. Revoking one leaves everything else untouched.

## Building from source

```bash
git clone https://github.com/Aster-Privacy/Aster-Send.git
cd Aster-Send
python -m unittest discover -s tests
pip install .
```

The tests use only the standard library, so there is nothing to install first.

New app configurations are welcome. Add an entry to `aster_send/presets.py`, and the test suite checks that it renders.

## License

Released under the [MIT License](LICENSE). Use it, change it, and ship it in anything you like.

## Community

Join our [Discord](https://discord.gg/R4XqRUfgWZ) to share feedback, ask questions, and contribute to the privacy community. You can also find us on [X](https://x.com/AsterPrivacy) and [Reddit](https://www.reddit.com/r/AsterPrivacy).

If you have any questions or security disclosures, email us at [hello@astermail.org](mailto:hello@astermail.org) or [security@astermail.org](mailto:security@astermail.org). **Do not open a public issue for security vulnerabilities.** Read [SECURITY.md](https://github.com/Aster-Privacy/.github/blob/main/SECURITY.md) for the full security vulnerability disclosure process.

## Contributing

We welcome contributions of all kinds. Read [CONTRIBUTING.md](https://github.com/Aster-Privacy/.github/blob/main/CONTRIBUTING.md) before opening a pull request.

By contributing to this repository, you agree that your contributions will be licensed under the [MIT License](LICENSE).
