# gmail-automation

A small Python script that keeps a Gmail account "active" by authenticating with the
Gmail API over OAuth 2.0 and making one lightweight read-only call. It is meant to be
run on a schedule (cron, systemd timer, Task Scheduler) so an otherwise idle account
shows regular API activity.

## Features

- OAuth 2.0 installed-app flow against the Gmail API (`gmail.readonly` scope)
- Caches the authorised token in `token.json` and renews it with its refresh token, so
  later runs need no browser
- A scheduled run never waits for a browser: if a manual login is needed it logs how to
  do it and exits 1
- YAML config for the client-secret and token file paths
- Makefile targets for venv setup, running and tests, all using the project's own venv

## Requirements

- Python 3.9+
- A Google Cloud project with the Gmail API enabled and an OAuth client of type
  "Desktop app"; download its JSON as `client_secret.json`
- A browser on the machine for the first login (the consent flow opens a local server)

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

| Key | Default | Meaning |
| --- | --- | --- |
| `client_secret_path` | `client_secret.json` | Path to the OAuth client JSON from Google Cloud Console |
| `token_path` | `token.json` | Where the authorised token is cached |

A relative path in either setting is taken from the folder `keep_alive.py` is in,
wherever you start the script from; an absolute path is used as it is.

`scopes` and `accounts` are not supported: the scope is always `gmail.readonly` and one
run covers the single account in `token_path`. If a config still has either key, the
script logs one warning that it is ignored and carries on.

The config file must be a YAML mapping. `{}` is enough and means "use the defaults". A
file that is empty, has only comments, is not valid YAML or holds something else (a
list, a plain string) stops the run with exit code 1 and one error line that names the
file and says what is wrong.

`config.yaml`, `client_secret.json` and `token.json` are gitignored. Never commit them.

The token file holds the refresh token and the OAuth client secret, which together give
read access to all mail in the account. The script always writes it owner-only (mode
`0600`, whatever the umask, also when an older, wider-readable file is already there)
and atomically: the new content goes to a temporary file in the same folder and is then
moved over the old one, so a crash partway leaves the previous token intact.
`client_secret.json` is yours to protect (`chmod 600 client_secret.json`).

## Usage

```bash
make run
```

or:

```bash
./venv/bin/python keep_alive.py --config config.yaml
```

`make run` does exactly that, so both use the packages `make install` put into `venv`.

A relative `--config` path is looked up in the folder you run the command from, like any
other command-line path. So from another folder this works, with `my-config.yaml` in
that folder:

```bash
/path/to/gmail-automation/venv/bin/python /path/to/gmail-automation/keep_alive.py --config my-config.yaml
```

The paths inside the config are different: relative ones are taken from the script's
folder (see Configuration).

### First login

Run it once by hand, on a machine with a browser:

```bash
./venv/bin/python keep_alive.py --config config.yaml --interactive
```

A browser opens Google's consent page; after you approve, the token is saved to
`token_path`. The script waits at most 5 minutes for the login and then exits with an
error. The browser login only ever starts when you pass `--interactive` or when the
script is run from a terminal (standard input is a terminal).

### Scheduled runs

Schedule it daily, for example with cron:

```cron
0 9 * * * cd /path/to/gmail-automation && ./venv/bin/python keep_alive.py --config config.yaml
```

Later runs need no browser. The access token expires after about an hour; each run
renews it with the saved refresh token and writes the result back to `token_path`. Only
the token file is needed for that.

If Google rejects the refresh token (you revoked the app's access, changed the account
password in some cases, or the token expired, which happens after 7 days while the OAuth
consent screen is in "Testing" status), a manual login is needed again. A scheduled run
does not open a browser or wait for one: it logs one error saying a manual login is
needed and exits with code 1. Fix it by running the first-login command above once by
hand. The same happens if the token file is missing.

If the token file exists but cannot be read (corrupted JSON or missing required fields
such as `refresh_token`), the script stops with exit code 1 and logs an error that names
the file and says how to fix it (delete the file and run the first-login command with
`--interactive`). The token file is never deleted or modified by the script.

If `token_path` points to a folder that does not exist, the browser login is never
started: the script logs an error naming the missing folder and exits with code 1 before
any OAuth consent flow begins. Create the folder first, then run the first-login command.

## Status

Early prototype. The tests cover it against a fake Gmail; it has not been verified
against a real Google account. Known gaps:

- Only one account is supported: one run covers the single account whose token is in
  `token_path`. To cover more accounts, schedule one run per account, each with its own
  config and token file.

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
and `pytest` on Python 3.9 and 3.15, plus `make install`, `make test` and a `make run`
that must get as far as the missing client secret, on every pull request and on every
push to `master`.

`requirements.txt` gives each package a lower bound and an upper bound below its next
major version.

## License

MIT, see [LICENSE](LICENSE).
