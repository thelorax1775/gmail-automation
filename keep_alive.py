#!/usr/bin/env python3
"""
Gmail Keep-Alive Script

This script performs a simple keep-alive operation for Gmail accounts
by ensuring the account appears active via a lightweight API call.

Usage:
    python keep_alive.py --config config.yaml
"""

import argparse
import logging
import os
import sys

import yaml
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# Scopes required
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def load_config(config_path: str) -> dict:
    """Load configuration from a YAML file."""
    if not os.path.exists(config_path):
        logging.error("Config file not found: %s", config_path)
        sys.exit(1)
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


def get_gmail_service(config: dict):
    """Authenticate and return a Gmail API service object."""
    creds = None
    token_path = config.get("token_path", "token.json")
    client_secret_path = config.get("client_secret_path", "client_secret.json")

    # Load existing token if it exists
    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)

    # If no valid credentials, run the OAuth flow
    if not creds or not creds.valid:
        if os.path.exists(client_secret_path):
            flow = InstalledAppFlow.from_client_secrets_file(
                client_secret_path, SCOPES
            )
            creds = flow.run_local_server(port=0)
            # Save the credentials for the next run
            with open(token_path, "w") as token_file:
                token_file.write(creds.to_json())
        else:
            logging.error(
                "Missing client secrets file: %s", client_secret_path
            )
            sys.exit(1)

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
    args = parser.parse_args()

    # Ensure script is run from its directory
    script_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(script_dir)

    # Load configuration
    config = load_config(args.config)

    # Perform a lightweight API call to keep the account active
    try:
        service = get_gmail_service(config)
        # Example: get profile (lightweight)
        profile = service.users().profile().get().execute()
        logging.info(
            "Keep-alive successful for account: %s", profile.get("emailAddress")
        )
    except Exception as e:
        logging.error("Error during keep-alive: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()