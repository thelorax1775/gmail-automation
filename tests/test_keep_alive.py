"""Tests for keep_alive.py.

Nothing here talks to Google. The Gmail client is the real google-api-python-client
one, built from the API description bundled with the library
(``static_discovery=True``) and answering from canned HTTP replies. Every
credential below is a made-up placeholder.
"""

import json
import logging
import sys
from unittest import mock
from urllib.parse import urlsplit

import pytest
from googleapiclient.discovery import build
from googleapiclient.http import HttpMockSequence

import keep_alive

FAKE_ADDRESS = "canned-user@example.com"


def fake_gmail(*replies):
    """Return (service, http): a Gmail client that answers from canned replies.

    ``http.request_sequence`` records each request as (uri, method, body, headers).
    """
    http = HttpMockSequence(list(replies))
    return build("gmail", "v1", http=http, static_discovery=True), http


def write_config(tmp_path, **values):
    path = tmp_path / "config.yaml"
    path.write_text(json.dumps(values))  # JSON is valid YAML
    return path


def write_fake_token(tmp_path):
    """A syntactically valid, unexpired authorised-user file with fake values."""
    path = tmp_path / "token.json"
    path.write_text(
        json.dumps(
            {
                "token": "fake-access-token",
                "refresh_token": "fake-refresh-token",
                "token_uri": "https://oauth2.googleapis.com/token",
                "client_id": "fake-client-id",
                "client_secret": "fake-client-secret",
                "scopes": keep_alive.SCOPES,
                "expiry": "2999-01-01T00:00:00Z",
            }
        )
    )
    return path


@pytest.fixture
def run_main(monkeypatch, tmp_path, caplog):
    """Call keep_alive.main() as ``keep_alive.py --config <path>``.

    main() changes into the script's folder; monkeypatch.chdir() makes pytest
    put the working directory back afterwards.
    """
    monkeypatch.chdir(tmp_path)
    caplog.set_level(logging.INFO)

    def _run(config_path):
        monkeypatch.setattr(
            sys, "argv", ["keep_alive.py", "--config", str(config_path)]
        )
        return keep_alive.main()

    return _run


def test_successful_run_requests_the_profile_and_logs_the_address(
    run_main, tmp_path, caplog
):
    service, http = fake_gmail(
        ({"status": "200"}, json.dumps({"emailAddress": FAKE_ADDRESS}))
    )
    config = write_config(tmp_path)

    with mock.patch.object(
        keep_alive, "get_gmail_service", return_value=service
    ) as login:
        run_main(config)  # must return, not raise SystemExit

    login.assert_called_once()
    assert len(http.request_sequence) == 1
    uri, method = http.request_sequence[0][:2]
    assert method == "GET"
    assert urlsplit(uri).path == "/gmail/v1/users/me/profile"
    assert FAKE_ADDRESS in caplog.text
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


def test_gmail_error_exits_1_and_logs_an_error(run_main, tmp_path, caplog):
    service, http = fake_gmail(({"status": "500"}, "{}"))
    config = write_config(tmp_path)

    with mock.patch.object(keep_alive, "get_gmail_service", return_value=service), pytest.raises(
        SystemExit
    ) as exit_info:
        run_main(config)

    assert exit_info.value.code == 1
    # The request was really made and it was the canned 500 that failed the run.
    assert urlsplit(http.request_sequence[0][0]).path == "/gmail/v1/users/me/profile"
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors
    assert "Error during keep-alive" in errors[0].getMessage()
    assert "500" in errors[0].getMessage()


def test_missing_config_file_exits_1(run_main, tmp_path, caplog):
    with mock.patch.object(keep_alive, "get_gmail_service") as login, pytest.raises(
        SystemExit
    ) as exit_info:
        run_main(tmp_path / "does-not-exist.yaml")

    assert exit_info.value.code == 1
    assert "Config file not found" in caplog.text
    login.assert_not_called()


def test_missing_client_secret_without_token_exits_1(run_main, tmp_path, caplog):
    config = write_config(
        tmp_path,
        token_path=str(tmp_path / "token.json"),
        client_secret_path=str(tmp_path / "client_secret.json"),
    )

    with (
        mock.patch.object(keep_alive, "InstalledAppFlow") as flow,
        mock.patch.object(keep_alive, "build") as build_client,
        pytest.raises(SystemExit) as exit_info,
    ):
        run_main(config)

    assert exit_info.value.code == 1
    assert "Missing client secrets file" in caplog.text
    flow.from_client_secrets_file.assert_not_called()
    build_client.assert_not_called()
    assert not (tmp_path / "token.json").exists()


def test_valid_token_is_used_without_the_browser_flow(tmp_path):
    token = write_fake_token(tmp_path)
    before = token.read_text()
    config = {
        "token_path": str(token),
        # Present on purpose: the flow must be skipped because the token is
        # valid, not because the client secret is missing.
        "client_secret_path": str(tmp_path / "client_secret.json"),
    }
    (tmp_path / "client_secret.json").write_text("{}")

    with mock.patch.object(keep_alive, "InstalledAppFlow") as flow, mock.patch.object(
        keep_alive, "build"
    ) as build_client:
        service = keep_alive.get_gmail_service(config)

    flow.from_client_secrets_file.assert_not_called()
    assert flow.mock_calls == []
    build_client.assert_called_once()
    args, kwargs = build_client.call_args
    assert args == ("gmail", "v1")
    assert kwargs["credentials"].token == "fake-access-token"
    assert kwargs["credentials"].valid
    assert service is build_client.return_value
    assert token.read_text() == before


def test_valid_token_end_to_end_run_succeeds(run_main, tmp_path, caplog):
    """main() with a saved token: real login code path, canned Gmail reply."""
    token = write_fake_token(tmp_path)
    config = write_config(
        tmp_path,
        token_path=str(token),
        client_secret_path=str(tmp_path / "client_secret.json"),
    )
    service, http = fake_gmail(
        ({"status": "200"}, json.dumps({"emailAddress": FAKE_ADDRESS}))
    )

    with mock.patch.object(keep_alive, "InstalledAppFlow") as flow, mock.patch.object(
        keep_alive, "build", return_value=service
    ):
        run_main(config)

    assert flow.mock_calls == []
    assert urlsplit(http.request_sequence[0][0]).path == "/gmail/v1/users/me/profile"
    assert FAKE_ADDRESS in caplog.text


# --- New tests for broken token file and missing token folder ---


def test_broken_token_file_exits_1_and_logs_clear_error(tmp_path, caplog):
    """Token file with invalid JSON -> SystemExit 1, clear error, file unchanged."""
    token_path = tmp_path / "token.json"
    broken_content = '{"refresh_token": "***", "client_id"'
    token_path.write_text(broken_content)
    config = {
        "token_path": str(token_path),
        "client_secret_path": str(tmp_path / "client_secret.json"),
    }
    (tmp_path / "client_secret.json").write_text("{}")

    with (
        mock.patch.object(keep_alive, "InstalledAppFlow") as flow,
        mock.patch.object(keep_alive, "build") as build_client,
        pytest.raises(SystemExit) as exit_info,
    ):
        keep_alive.get_gmail_service(config)

    assert exit_info.value.code == 1
    assert "Token file" in caplog.text
    assert str(token_path) in caplog.text
    assert "cannot be used" in caplog.text
    assert "--interactive" in caplog.text
    flow.from_client_secrets_file.assert_not_called()
    build_client.assert_not_called()
    assert token_path.read_text() == broken_content


def test_token_file_missing_refresh_token_exits_1_and_logs_clear_error(tmp_path, caplog):
    """Valid JSON without refresh_token -> SystemExit 1, clear error, file unchanged."""
    token_path = tmp_path / "token.json"
    valid_but_bad = '{"client_id": "a", "client_secret": "***", "token": "t"}'
    token_path.write_text(valid_but_bad)
    config = {
        "token_path": str(token_path),
        "client_secret_path": str(tmp_path / "client_secret.json"),
    }
    (tmp_path / "client_secret.json").write_text("{}")

    with (
        mock.patch.object(keep_alive, "InstalledAppFlow") as flow,
        mock.patch.object(keep_alive, "build") as build_client,
        pytest.raises(SystemExit) as exit_info,
    ):
        keep_alive.get_gmail_service(config)

    assert exit_info.value.code == 1
    assert "Token file" in caplog.text
    assert str(token_path) in caplog.text
    assert "cannot be used" in caplog.text
    assert "--interactive" in caplog.text
    flow.from_client_secrets_file.assert_not_called()
    build_client.assert_not_called()
    assert token_path.read_text() == valid_but_bad


def test_missing_token_folder_exits_1_before_browser_login(tmp_path, caplog):
    """token_path folder doesn't exist -> SystemExit 1, clear error, flow not called."""
    token_path = tmp_path / "missing" / "token.json"  # parent dir 'missing' doesn't exist
    config = {
        "token_path": str(token_path),
        "client_secret_path": str(tmp_path / "client_secret.json"),
    }
    (tmp_path / "client_secret.json").write_text("{}")

    with (
        mock.patch.object(keep_alive, "InstalledAppFlow") as flow,
        mock.patch.object(keep_alive, "build") as build_client,
        pytest.raises(SystemExit) as exit_info,
    ):
        keep_alive.get_gmail_service(config)

    assert exit_info.value.code == 1
    assert "The folder for token_path does not exist" in caplog.text
    assert "missing" in caplog.text
    flow.from_client_secrets_file.assert_not_called()
    build_client.assert_not_called()