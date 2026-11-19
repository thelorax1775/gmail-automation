# gmail-automation

A small Python script that keeps a Gmail account "active" by authenticating with the
Gmail API over OAuth 2.0 and making one lightweight read-only call. It is meant to be
run on a schedule (cron, systemd timer, Task Scheduler) so an otherwise idle account
shows regular API activity.

## Features

- OAuth 2.0 installed-app flow against the Gmail API (`gmail.readonly` scope)
- Caches the authorised token in `token.json` so later runs are non-interactive
- YAML config for the client-secret and token file paths
- Makefile targets for venv setup, running and tests

## Requirements

- Python 3.9+
- A Google Cloud project with the Gmail API enabled and an OAuth client of type
  "Desktop app"; download its JSON as `client_secret.json`
- A browser on the machine for the first run (the consent flow opens a local server)

## Install

```bash
make install
```

or manually:

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
```

## Configuration

```bash
cp config.yaml.example config.yaml
```

| Key | Used | Meaning |
| --- | --- | --- |
| `client_secret_path` | yes | Path to the OAuth client JSON from Google Cloud Console |
| `token_path` | yes | Where the authorised token is cached |
| `scopes` | no | Ignored; the script hard-codes `gmail.readonly` |
| `accounts` | no | Ignored; one run covers the single account in `token_path` |

`config.yaml`, `client_secret.json` and `token.json` are gitignored. Never commit them.

## Usage

```bash
make run
```

or:

```bash
python3 keep_alive.py --config config.yaml
```

The script changes into its own directory before loading the config, so relative paths
resolve against the repository folder. Schedule it daily, for example with cron:

```cron
0 9 * * * cd /path/to/gmail-automation && ./venv/bin/python keep_alive.py --config config.yaml
```

## Status

Early prototype. The tests cover it against a fake Gmail; it has not been verified
against a real Google account. Known gaps:

- Expired tokens trigger a fresh interactive consent flow instead of a refresh.
- Only one account is supported despite the `accounts` key in the example config.

## Development

```bash
make install   # venv with requirements.txt and requirements-dev.txt (pytest, ruff)
make test      # runs pytest from the venv
```

or without make: `./venv/bin/python -m pytest` and `./venv/bin/ruff check .`

The tests never contact Google and need no account, client secret or token: the Gmail
client is built from the API description bundled with `google-api-python-client` and
answers from canned HTTP replies, and `pytest-socket` (`--disable-socket` in
`pytest.ini`) fails any test that tries to open a network connection.

GitHub Actions (`.github/workflows/ci.yml`) runs `ruff check .` (rules in `ruff.toml`)
and `pytest` on Python 3.9 and 3.15, plus `make install && make test`, on every pull
request and on every push to `master`.

## License

MIT, see [LICENSE](LICENSE).
