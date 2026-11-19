#!/usr/bin/env python3
"""
Gmail Keep-Alive Script

This script performs a simple keep-alive operation for Gmail accounts
by ensuring the account appears active via a lightweight API call.

Usage:
    python keep_alive.py --config config.yaml [--interactive]
"""

import argparse
import logging
import os
import sys
import tempfile

import yaml
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# Scopes required
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

# How long the browser login waits for the person before giving up.
LOGIN_TIMEOUT_SECONDS = 300


# Config keys the script reads, with the value used when the key is absent.
PATH_DEFAULTS = {
    "client_secret_path": "client_secret.json",
    "token_path": "token.json",
}

# Keys older example configs offered that the script has never read.
UNSUPPORTED_KEYS = ("scopes", "accounts")


def load_config(config_path: str) -> dict:
    """Load configuration from a YAML file.

    The top level must be a mapping; an empty mapping (``{}``) is fine and
    means "use the defaults". Anything else logs one error naming the file and
    exits with code 1.
    """
    if not os.path.exists(config_path):
        logging.error("Config file not found: %s", config_path)
        sys.exit(1)
    try:
        with open(config_path, "r") as f:
            config = yaml.safe_load(f)
    except (OSError, UnicodeDecodeError) as e:
        logging.error("Config file %s cannot be read: %s", config_path, e)
        sys.exit(1)
    except yaml.YAMLError as e:
        logging.error(
            "Config file %s is not valid YAML: %s",
            config_path,
            " ".join(str(e).split()),
        )
        sys.exit(1)
    if config is None:
        logging.error(
            "Config file %s is empty: it must contain a YAML mapping of "
            "settings such as token_path (see config.yaml.example)",
            config_path,
        )
        sys.exit(1)
    if not isinstance(config, dict):
        logging.error(
            "Config file %s must contain a YAML mapping of settings such as "
            "token_path at the top level, but it contains a %s "
            "(see config.yaml.example)",
            config_path,
            type(config).__name__,
        )
        sys.exit(1)
    for key in PATH_DEFAULTS:
        if key in config and not (isinstance(config[key], str) and config[key]):
            logging.error(
                "Config file %s: %s must be a file path, but it is %r",
                config_path,
                key,
                config[key],
            )
            sys.exit(1)
    for key in UNSUPPORTED_KEYS:
        if key in config:
            logging.warning(
                "Config file %s: the %s setting is not supported and is ignored",
                config_path,
                key,
            )
    return config


def resolve_paths(config: dict, base_dir: str) -> dict:
    """Return a copy of ``config`` with both file paths made absolute.

    A relative ``token_path`` or ``client_secret_path`` (and the default used
    when the key is absent) is taken relative to ``base_dir``; an absolute
    path is kept as it is.
    """
    resolved = dict(config)
    for key, default in PATH_DEFAULTS.items():
        resolved[key] = os.path.join(base_dir, config.get(key, default))
    return resolved


def save_token(token_path: str, token_json: str) -> None:
    """Write the token file owner-only (0600) and atomically.

    The file holds the refresh token and the OAuth client secret, so it must not
    be readable by other users whatever the umask is. The data goes to a
    temporary file in the same directory first and is then moved over the old
    file, so a crash partway leaves the previous token file untouched.
    """
    directory = os.path.dirname(token_path) or "."
    # mkstemp creates the file with mode 0600 regardless of the umask.
    fd, tmp_path = tempfile.mkstemp(
        dir=directory, prefix=os.path.basename(token_path) + ".", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w") as tmp_file:
            tmp_file.write(token_json)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, token_path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except FileNotFoundError:
            pass
        raise


def get_gmail_service(config: dict, interactive: bool = False):
    """Authenticate and return a Gmail API service object.

    A saved token that has expired is renewed with its refresh token, without a
    browser. The browser login only starts when ``interactive`` is true or
    standard input is a terminal; otherwise the process logs what to do and
    exits with code 1 instead of waiting for a browser that is not there.
    """
    creds = None
    token_path = config.get("token_path", "token.json")
    client_secret_path = config.get("client_secret_path", "client_secret.json")

    # Load existing token if it exists
    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)

    # Renew an expired access token silently with the refresh token
    if creds and not creds.valid and creds.refresh_token:
        try:
            creds.refresh(Request())
        except RefreshError as e:
            # The refresh token was revoked or has expired: a new login is needed.
            logging.warning("Could not renew the saved login: %s", e)
            creds = None
        else:
            save_token(token_path, creds.to_json())

    # If there are still no valid credentials, a person has to log in
    if not creds or not creds.valid:
        if not os.path.exists(client_secret_path):
            logging.error(
                "Missing client secrets file: %s", client_secret_path
            )
            sys.exit(1)
        if not (interactive or sys.stdin.isatty()):
            logging.error(
                "A manual login is needed and this run has no terminal. "
                "Run once by hand with --interactive to log in through a "
                "browser: python3 keep_alive.py --config <config> --interactive"
            )
            sys.exit(1)
        flow = InstalledAppFlow.from_client_secrets_file(
            client_secret_path, SCOPES
        )
        creds = flow.run_local_server(
            port=0, timeout_seconds=LOGIN_TIMEOUT_SECONDS
        )
        # Save the credentials for the next run
        save_token(token_path, creds.to_json())

    # Build the Gmail service
    service = build("gmail", "v1", credentials=creds)
    return service


def main():
    parser = argparse.ArgumentParser(
        description="Gmail Keep-Alive script"
    )
    parser.add_argument(
        "--config",
        required=True,
        help="Path to the YAML configuration file",
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help=(
            "Allow the browser login even when standard input is not a "
            "terminal (use for the first login or when a new login is needed)"
        ),
    )
    args = parser.parse_args()

    # A relative --config is looked up where the command was started.
    config_path = os.path.abspath(args.config)
    config = load_config(config_path)

    # Relative paths inside the config are taken from the script's folder.
    script_dir = os.path.dirname(os.path.abspath(__file__))
    config = resolve_paths(config, script_dir)

    # Perform a lightweight API call to keep the account active
    try:
        service = get_gmail_service(config, interactive=args.interactive)
        # Get the account's profile (lightweight, read-only)
        profile = service.users().getProfile(userId="me").execute()
        logging.info(
            "Keep-alive successful for account: %s", profile.get("emailAddress")
        )
    except Exception as e:
        logging.error("Error during keep-alive: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()