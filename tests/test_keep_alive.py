"""Tests for keep_alive.py.

Nothing here talks to Google. The Gmail client is the real google-api-python-client
one, built from the API description bundled with the library
(``static_discovery=True``) and answering from canned HTTP replies. Every
credential below is a made-up placeholder.
"""

import datetime
import json
import logging
import os
import stat
import sys
from unittest import mock
from urllib.parse import urlsplit

import pytest
from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials
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

    with mock.patch.object(keep_alive, "get_gmail_service", return_value=service):
        with pytest.raises(SystemExit) as exit_info:
            run_main(config)

    assert exit_info.value.code == 1
    # The request was really made and it was the canned 500 that failed the run.
    assert urlsplit(http.request_sequence[0][0]).path == "/gmail/v1/users/me/profile"
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors
    assert "Error during keep-alive" in errors[0].getMessage()
    assert "500" in errors[0].getMessage()


def test_missing_config_file_exits_1(run_main, tmp_path, caplog):
    with mock.patch.object(keep_alive, "get_gmail_service") as login:
        with pytest.raises(SystemExit) as exit_info:
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

    with mock.patch.object(keep_alive, "InstalledAppFlow") as flow, mock.patch.object(
        keep_alive, "build"
    ) as build_client:
        with pytest.raises(SystemExit) as exit_info:
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


# --- Token refresh, the login guard and the token file -----------------------


def write_expired_token(tmp_path, mode=None):
    """An authorised-user file whose access token expired in 2020. Fake values."""
    path = tmp_path / "token.json"
    path.write_text(
        json.dumps(
            {
                "token": "fake-expired-access-token",
                "refresh_token": "fake-refresh-token",
                "token_uri": "https://oauth2.googleapis.com/token",
                "client_id": "fake-client-id",
                "client_secret": "fake-client-secret",
                "scopes": keep_alive.SCOPES,
                "expiry": "2020-01-01T00:00:00Z",
            }
        )
    )
    if mode is not None:
        path.chmod(mode)
    return path


def login_config(tmp_path, client_secret=True):
    """Config dict pointing at tmp_path; optionally with a (fake) client secret."""
    if client_secret:
        (tmp_path / "client_secret.json").write_text("{}")
    return {
        "token_path": str(tmp_path / "token.json"),
        "client_secret_path": str(tmp_path / "client_secret.json"),
    }


def fake_refresh(creds, request):
    """Stands in for Credentials.refresh: what a successful renewal leaves behind."""
    creds.token = "fake-refreshed-access-token"
    creds.expiry = datetime.datetime(2999, 1, 1)


def fake_flow_credentials():
    """What the browser flow would hand back after a login. Fake values."""
    return Credentials(
        token="fake-new-login-access-token",
        refresh_token="fake-new-login-refresh-token",
        token_uri="https://oauth2.googleapis.com/token",
        client_id="fake-client-id",
        client_secret="fake-client-secret",
        scopes=keep_alive.SCOPES,
        expiry=datetime.datetime(2999, 1, 1),
    )


def set_stdin_tty(monkeypatch, is_tty):
    stdin = mock.Mock()
    stdin.isatty.return_value = is_tty
    monkeypatch.setattr(keep_alive.sys, "stdin", stdin)


def file_mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


@pytest.fixture
def umask_022():
    old = os.umask(0o022)
    yield
    os.umask(old)


@pytest.fixture
def patched_login(monkeypatch):
    """Mock Credentials.refresh, InstalledAppFlow and build; stdin is not a tty.

    Returns a namespace with ``refresh``, ``flow`` (the InstalledAppFlow mock),
    ``run_local_server`` and ``build``.
    """
    set_stdin_tty(monkeypatch, False)
    with mock.patch.object(
        Credentials, "refresh", autospec=True, side_effect=fake_refresh
    ) as refresh, mock.patch.object(
        keep_alive, "InstalledAppFlow"
    ) as flow, mock.patch.object(keep_alive, "build") as build_client:
        run_local_server = flow.from_client_secrets_file.return_value.run_local_server
        run_local_server.return_value = fake_flow_credentials()
        yield mock.Mock(
            refresh=refresh,
            flow=flow,
            run_local_server=run_local_server,
            build=build_client,
        )


def test_expired_token_is_refreshed_without_the_browser_flow(tmp_path, patched_login):
    token = write_expired_token(tmp_path)
    config = login_config(tmp_path)

    service = keep_alive.get_gmail_service(config)

    patched_login.refresh.assert_called_once()
    assert patched_login.flow.mock_calls == []
    saved = json.loads(token.read_text())
    assert saved["token"] == "fake-refreshed-access-token"
    assert saved["refresh_token"] == "fake-refresh-token"
    assert saved["expiry"].startswith("2999-01-01")
    used = patched_login.build.call_args.kwargs["credentials"]
    assert used.token == "fake-refreshed-access-token"
    assert service is patched_login.build.return_value


def test_refresh_works_without_a_client_secret_file_or_a_terminal(
    tmp_path, patched_login
):
    """The usual cron run: only the token file is needed to renew the login."""
    token = write_expired_token(tmp_path)
    config = login_config(tmp_path, client_secret=False)

    keep_alive.get_gmail_service(config)

    patched_login.refresh.assert_called_once()
    assert patched_login.flow.mock_calls == []
    assert json.loads(token.read_text())["token"] == "fake-refreshed-access-token"


def test_refresh_error_falls_back_to_the_browser_flow_on_a_terminal(
    tmp_path, patched_login, monkeypatch
):
    token = write_expired_token(tmp_path)
    config = login_config(tmp_path)
    patched_login.refresh.side_effect = RefreshError("invalid_grant: fake revoked")
    set_stdin_tty(monkeypatch, True)

    keep_alive.get_gmail_service(config)

    patched_login.refresh.assert_called_once()
    patched_login.run_local_server.assert_called_once()
    saved = json.loads(token.read_text())
    assert saved["token"] == "fake-new-login-access-token"
    assert saved["refresh_token"] == "fake-new-login-refresh-token"
    used = patched_login.build.call_args.kwargs["credentials"]
    assert used.token == "fake-new-login-access-token"


def test_refresh_error_without_a_terminal_exits_1_and_says_how_to_log_in(
    tmp_path, patched_login, caplog
):
    token = write_expired_token(tmp_path)
    before = token.read_text()
    config = login_config(tmp_path)
    patched_login.refresh.side_effect = RefreshError("invalid_grant: fake revoked")

    with pytest.raises(SystemExit) as exit_info:
        keep_alive.get_gmail_service(config)

    assert exit_info.value.code == 1
    patched_login.refresh.assert_called_once()
    assert patched_login.flow.mock_calls == []
    patched_login.build.assert_not_called()
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1
    assert "manual login is needed" in errors[0].getMessage()
    assert "--interactive" in errors[0].getMessage()
    assert token.read_text() == before


def test_no_token_without_a_terminal_exits_1_without_the_browser_flow(
    tmp_path, patched_login, caplog
):
    config = login_config(tmp_path)

    with pytest.raises(SystemExit) as exit_info:
        keep_alive.get_gmail_service(config)

    assert exit_info.value.code == 1
    patched_login.run_local_server.assert_not_called()
    assert patched_login.flow.mock_calls == []
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1
    assert "manual login is needed" in errors[0].getMessage()
    assert "--interactive" in errors[0].getMessage()
    assert not (tmp_path / "token.json").exists()


def test_no_token_on_a_terminal_runs_the_browser_flow_with_a_timeout(
    tmp_path, patched_login, monkeypatch
):
    config = login_config(tmp_path)
    set_stdin_tty(monkeypatch, True)

    keep_alive.get_gmail_service(config)

    patched_login.run_local_server.assert_called_once()
    timeout = patched_login.run_local_server.call_args.kwargs["timeout_seconds"]
    assert isinstance(timeout, int) and 0 < timeout <= 300
    saved = json.loads((tmp_path / "token.json").read_text())
    assert saved["token"] == "fake-new-login-access-token"


def test_interactive_argument_runs_the_browser_flow_without_a_terminal(
    tmp_path, patched_login
):
    config = login_config(tmp_path)

    keep_alive.get_gmail_service(config, interactive=True)

    patched_login.run_local_server.assert_called_once()
    timeout = patched_login.run_local_server.call_args.kwargs["timeout_seconds"]
    assert 0 < timeout <= 300


def test_interactive_flag_on_the_command_line_allows_the_browser_flow(
    run_main, tmp_path, patched_login, monkeypatch, caplog
):
    """``--interactive`` reaches the login code; without it the same run exits 1."""
    cfg = login_config(tmp_path)
    config = write_config(tmp_path, **cfg)
    service, _ = fake_gmail(
        ({"status": "200"}, json.dumps({"emailAddress": FAKE_ADDRESS}))
    )
    patched_login.build.return_value = service

    with pytest.raises(SystemExit) as exit_info:
        run_main(config)
    assert exit_info.value.code == 1
    patched_login.run_local_server.assert_not_called()

    monkeypatch.setattr(
        sys, "argv", ["keep_alive.py", "--config", str(config), "--interactive"]
    )
    keep_alive.main()

    patched_login.run_local_server.assert_called_once()
    assert FAKE_ADDRESS in caplog.text


def test_new_token_file_is_owner_only_under_umask_022(
    tmp_path, patched_login, umask_022
):
    config = login_config(tmp_path)

    keep_alive.get_gmail_service(config, interactive=True)

    assert file_mode(tmp_path / "token.json") == 0o600


def test_existing_0644_token_file_becomes_owner_only_on_refresh(
    tmp_path, patched_login, umask_022
):
    token = write_expired_token(tmp_path, mode=0o644)
    assert file_mode(token) == 0o644
    config = login_config(tmp_path)

    keep_alive.get_gmail_service(config)

    assert json.loads(token.read_text())["token"] == "fake-refreshed-access-token"
    assert file_mode(token) == 0o600


def test_existing_0644_token_file_becomes_owner_only_on_new_login(
    tmp_path, patched_login, umask_022
):
    token = write_expired_token(tmp_path, mode=0o644)
    config = login_config(tmp_path)
    patched_login.refresh.side_effect = RefreshError("invalid_grant: fake revoked")

    keep_alive.get_gmail_service(config, interactive=True)

    assert json.loads(token.read_text())["token"] == "fake-new-login-access-token"
    assert file_mode(token) == 0o600


def test_save_token_with_a_bare_file_name_writes_into_the_current_directory(
    tmp_path, monkeypatch, umask_022
):
    monkeypatch.chdir(tmp_path)

    keep_alive.save_token("token.json", '{"fake": true}')

    assert (tmp_path / "token.json").read_text() == '{"fake": true}'
    assert file_mode(tmp_path / "token.json") == 0o600
    assert os.listdir(tmp_path) == ["token.json"]


def test_failed_write_leaves_the_previous_token_file_intact(tmp_path, patched_login):
    token = write_expired_token(tmp_path)
    before = token.read_text()
    config = login_config(tmp_path)
    real_replace = os.replace
    seen = []

    def failing_replace(src, dst):
        # The new token must already be complete in a temporary file next to
        # the real one: that is what makes the swap atomic.
        seen.append((src, dst, open(src).read(), file_mode(src)))
        raise OSError("fake failure partway through the write")

    with mock.patch.object(keep_alive.os, "replace", side_effect=failing_replace):
        with pytest.raises(OSError, match="fake failure"):
            keep_alive.get_gmail_service(config)

    assert os.replace is real_replace
    assert len(seen) == 1
    src, dst, written, mode = seen[0]
    assert dst == str(token)
    assert os.path.dirname(src) == str(tmp_path) and src != dst
    assert json.loads(written)["token"] == "fake-refreshed-access-token"
    assert mode == 0o600
    assert token.read_text() == before
    # The temporary file is cleaned up, so no stray copy of the token is left.
    assert sorted(os.listdir(tmp_path)) == ["client_secret.json", "token.json"]
