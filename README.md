# gmail-automation

A small Python script that keeps a Gmail account "active" by authenticating with the
Gmail API over OAuth 2.0 and making one lightweight read-only call. It is meant to be
run on a schedule (cron, systemd timer, Task Scheduler) so an otherwise idle account
shows regular API activity.

## Features

- OAuth 2.0 installed-app flow against the Gmail API (`gmail.readonly` scope)
- Caches the authorised token in `token.json` so later runs are non-interactive
- YAML config for the client-secret and token file paths
- Makefile targets for venv setup and running

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

Early prototype, not verified end to end. Known gaps:

- The keep-alive call uses `users().profile().get()`, which is not a Gmail API method
  (the real one is `users().getProfile(userId="me")`), so a run is expected to log an
  error and exit 1 after authenticating.
- Expired tokens trigger a fresh interactive consent flow instead of a refresh.
- Only one account is supported despite the `accounts` key in the example config.
- No tests (`make test` is a placeholder).

## License

MIT, see [LICENSE](LICENSE).
