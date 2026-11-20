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

    The run starts in tmp_path; monkeypatch.chdir() makes pytest put the
    working directory back afterwards.
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


def test_invalid_json_token_exits_1(tmp_path, caplog):
    """Invalid JSON in token file -> SystemExit 1, clear error, file unchanged."""
    token_path = tmp_path / "token.json"
    token_path.write_text('{"refresh_token": "***", "client_id"')
    before = token_path.read_bytes()

    config = {
        "token_path": str(token_path),
        "client_secret_path": str(tmp_path / "client_secret.json"),
    }
    (tmp_path / "client_secret.json").write_text("{}")

    with mock.patch.object(keep_alive, "InstalledAppFlow") as flow, mock.patch.object(
        keep_alive, "build"
    ):
        with pytest.raises(SystemExit) as exit_info:
            keep_alive.get_gmail_service(config)

    assert exit_info.value.code == 1
    assert "Token file" in caplog.text
    assert str(token_path) in caplog.text
    assert "cannot be used" in caplog.text
    assert "--interactive" in caplog.text
    assert token_path.read_bytes() == before  # file unchanged
    flow.from_client_secrets_file.assert_not_called()


def test_token_missing_refresh_token_exits_1_and_logs_clear_error(
    tmp_path, caplog
):
    """Valid JSON without refresh_token -> SystemExit 1, clear error, file unchanged."""
    token_path = tmp_path / "token.json"
    token_path.write_text(
        json.dumps(
            {
                "client_id": "a",
                "client_secret": "***",
                "token": "t",
            }
        )
    )
    before = token_path.read_bytes()

    config = {
        "token_path": str(token_path),
        "client_secret_path": str(tmp_path / "client_secret.json"),
    }
    (tmp_path / "client_secret.json").write_text("{}")

    with mock.patch.object(keep_alive, "InstalledAppFlow") as flow, mock.patch.object(
        keep_alive, "build"
    ):
        with pytest.raises(SystemExit) as exit_info:
            keep_alive.get_gmail_service(config)

    assert exit_info.value.code == 1
    assert "Token file" in caplog.text
    assert str(token_path) in caplog.text
    assert "cannot be used" in caplog.text
    assert "--interactive" in caplog.text
    assert token_path.read_bytes() == before  # file unchanged
    flow.from_client_secrets_file.assert_not_called()


@pytest.mark.parametrize(
    "bad_content",
    [
        "[]",
        "null",
        '"abc"',
        "42",
    ],
    ids=["list", "null", "string", "number"],
)
def test_wrong_shape_token_exits_1(tmp_path, caplog, bad_content):
    """Token file holds valid JSON of the wrong shape -> SystemExit 1, clear error."""
    token_path = tmp_path / "token.json"
    token_path.write_text(bad_content)
    before = token_path.read_bytes()

    config = {
        "token_path": str(token_path),
        "client_secret_path": str(tmp_path / "client_secret.json"),
    }
    (tmp_path / "client_secret.json").write_text("{}")

    with mock.patch.object(keep_alive, "InstalledAppFlow") as flow, mock.patch.object(
        keep_alive, "build"
    ):
        with pytest.raises(SystemExit) as exit_info:
            keep_alive.get_gmail_service(config)

    assert exit_info.value.code == 1
    assert "Token file" in caplog.text
    assert str(token_path) in caplog.text
    assert "cannot be used" in caplog.text
    assert "--interactive" in caplog.text
    assert token_path.read_bytes() == before  # file unchanged
    flow.from_client_secrets_file.assert_not_called()


@pytest.mark.parametrize(
    "bad_content",
    [
        "not json",
        "[]",
        "{}",
        '{"installed": "x"}',
    ],
    ids=["not-json", "empty-list", "empty-object", "wrong-shape"],
)
def test_unusable_client_secrets_exits_1(tmp_path, caplog, bad_content):
    """Client secrets file not JSON or wrong shape -> SystemExit 1, clear error."""
    token_path = tmp_path / "token.json"
    client_secret_path = tmp_path / "client_secret.json"
    client_secret_path.write_text(bad_content)

    config = {
        "token_path": str(token_path),
        "client_secret_path": str(client_secret_path),
    }

    with mock.patch.object(
        keep_alive.InstalledAppFlow, "run_local_server"
    ) as run_local_server, mock.patch.object(keep_alive, "build"):
        with pytest.raises(SystemExit) as exit_info:
            keep_alive.get_gmail_service(config, interactive=True)

    assert exit_info.value.code == 1
    assert "Client secrets file" in caplog.text
    assert str(client_secret_path) in caplog.text
    assert "cannot be used" in caplog.text
    assert "Desktop app" in caplog.text
    run_local_server.assert_not_called()
    assert not token_path.exists()


def test_missing_token_folder_exits_1_before_browser_login(tmp_path, caplog):
    """token_path folder doesn't exist -> SystemExit 1, clear error, flow not called."""
    token_path = tmp_path / "missing" / "token.json"

    config = {
        "token_path": str(token_path),
        "client_secret_path": str(tmp_path / "client_secret.json"),
    }
    (tmp_path / "client_secret.json").write_text("{}")

    with mock.patch.object(keep_alive, "InstalledAppFlow") as flow, mock.patch.object(
        keep_alive, "build"
    ):
        with pytest.raises(SystemExit) as exit_info:
            keep_alive.get_gmail_service(config, interactive=True)

    assert exit_info.value.code == 1
    assert "The folder for token_path does not exist" in caplog.text
    flow.from_client_secrets_file.assert_not_called()


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


# --- The --config path, the config file's content and unsupported keys -------

SCRIPT_DIR = os.path.dirname(os.path.abspath(keep_alive.__file__))


@pytest.fixture
def run_in(monkeypatch, caplog):
    """Call main() as ``keep_alive.py --config <argument>`` started in a folder.

    The login is replaced by a mock that hands back a canned Gmail client, so
    nothing is read or written at the paths the config names. Returns that
    mock; ``login.call_args.args[0]`` is the config main() passed to the login.
    """
    caplog.set_level(logging.INFO)

    def _run(start_dir, config_argument):
        monkeypatch.chdir(start_dir)
        monkeypatch.setattr(
            sys, "argv", ["keep_alive.py", "--config", str(config_argument)]
        )

        def canned_gmail(*args, **kwargs):
            # Built only when the login is reached, so a run that stops at the
            # config logs nothing but its own lines.
            return fake_gmail(
                ({"status": "200"}, json.dumps({"emailAddress": FAKE_ADDRESS}))
            )[0]

        with mock.patch.object(
            keep_alive, "get_gmail_service", side_effect=canned_gmail
        ) as login:
            try:
                keep_alive.main()
            finally:
                _run.login = login
        return login

    return _run


def records_at(caplog, level):
    return [r for r in caplog.records if r.levelno == level]


def test_relative_config_path_is_found_where_the_command_was_started(
    run_in, tmp_path, caplog
):
    start_dir = tmp_path / "started-here"
    start_dir.mkdir()
    assert os.path.realpath(start_dir) != os.path.realpath(SCRIPT_DIR)
    marker = str(tmp_path / "marker-token.json")
    (start_dir / "rel.yaml").write_text(json.dumps({"token_path": marker}))

    login = run_in(start_dir, "rel.yaml")

    login.assert_called_once()
    assert login.call_args.args[0]["token_path"] == marker
    assert "Config file not found" not in caplog.text
    assert FAKE_ADDRESS in caplog.text


def test_relative_config_path_in_a_subfolder_of_the_start_directory(
    run_in, tmp_path
):
    (tmp_path / "conf").mkdir()
    marker = str(tmp_path / "marker-token.json")
    (tmp_path / "conf" / "rel.yaml").write_text(json.dumps({"token_path": marker}))

    login = run_in(tmp_path, os.path.join("conf", "rel.yaml"))

    assert login.call_args.args[0]["token_path"] == marker


def test_relative_paths_inside_the_config_resolve_against_the_script_folder(
    run_in, tmp_path
):
    (tmp_path / "rel.yaml").write_text(
        'token_path: "state/token.json"\nclient_secret_path: "secret.json"\n'
    )

    login = run_in(tmp_path, "rel.yaml")

    config = login.call_args.args[0]
    assert config["token_path"] == os.path.join(SCRIPT_DIR, "state", "token.json")
    assert config["client_secret_path"] == os.path.join(SCRIPT_DIR, "secret.json")


def test_absolute_paths_inside_the_config_are_kept(run_in, tmp_path):
    token = str(tmp_path / "elsewhere" / "token.json")
    secret = str(tmp_path / "elsewhere" / "client_secret.json")
    config_file = write_config(tmp_path, token_path=token, client_secret_path=secret)

    login = run_in(tmp_path, config_file)

    config = login.call_args.args[0]
    assert config["token_path"] == token
    assert config["client_secret_path"] == secret


def test_empty_mapping_uses_the_documented_defaults_in_the_script_folder(
    run_in, tmp_path, caplog
):
    (tmp_path / "config.yaml").write_text("{}\n")

    login = run_in(tmp_path, "config.yaml")

    config = login.call_args.args[0]
    assert config["token_path"] == os.path.join(SCRIPT_DIR, "token.json")
    assert config["client_secret_path"] == os.path.join(
        SCRIPT_DIR, "client_secret.json"
    )
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


@pytest.mark.parametrize(
    "content, problem",
    [
        ("", "is empty"),
        ("\n\n", "is empty"),
        ("# only a comment\n# token_path: token.json\n", "is empty"),
        ("- token_path: token.json\n", "mapping"),
        ("just some text\n", "mapping"),
        ("42\n", "mapping"),
        ("token_path: [unclosed\n", "not valid YAML"),
        ("token_path: 5\n", "token_path must be a file path"),
        ("client_secret_path:\n", "client_secret_path must be a file path"),
    ],
    ids=[
        "empty",
        "blank-lines",
        "comments-only",
        "list",
        "string",
        "number",
        "invalid-yaml",
        "path-is-a-number",
        "path-is-blank",
    ],
)
def test_unusable_config_exits_1_with_one_line_naming_the_file(
    run_in, tmp_path, caplog, content, problem
):
    (tmp_path / "bad.yaml").write_text(content)

    with pytest.raises(SystemExit) as exit_info:
        run_in(tmp_path, "bad.yaml")

    assert exit_info.value.code == 1
    run_in.login.assert_not_called()
    assert len(caplog.records) == 1
    record = caplog.records[0]
    assert record.levelno == logging.ERROR
    message = record.getMessage()
    assert "\n" not in message
    assert str(tmp_path / "bad.yaml") in message
    assert problem in message
    assert "AttributeError" not in caplog.text
    assert "NoneType" not in caplog.text
    assert "has no attribute" not in caplog.text
    assert "Error during keep-alive" not in caplog.text


def test_unsupported_keys_get_one_warning_each_and_the_run_carries_on(
    run_in, tmp_path, caplog
):
    token = str(tmp_path / "token.json")
    (tmp_path / "old.yaml").write_text(
        json.dumps(
            {
                "token_path": token,
                "scopes": ["https://www.googleapis.com/auth/gmail.readonly"],
                "accounts": [{"label": "primary"}, {"label": "second"}],
            }
        )
    )

    login = run_in(tmp_path, "old.yaml")

    login.assert_called_once()
    assert login.call_args.args[0]["token_path"] == token
    assert FAKE_ADDRESS in caplog.text
    warnings = [r.getMessage() for r in records_at(caplog, logging.WARNING)]
    assert len(warnings) == 2
    for key in ("scopes", "accounts"):
        about_key = [w for w in warnings if "the %s setting" % key in w]
        assert len(about_key) == 1
        assert "ignored" in about_key[0]
        assert str(tmp_path / "old.yaml") in about_key[0]
    assert not records_at(caplog, logging.ERROR)


def test_only_the_unsupported_key_that_is_present_is_warned_about(
    run_in, tmp_path, caplog
):
    (tmp_path / "old.yaml").write_text("accounts: []\n")

    run_in(tmp_path, "old.yaml")

    warnings = [r.getMessage() for r in records_at(caplog, logging.WARNING)]
    assert len(warnings) == 1
    assert "the accounts setting" in warnings[0]


def test_example_config_offers_only_settings_the_script_reads(caplog):
    example = os.path.join(SCRIPT_DIR, "config.yaml.example")

    config = keep_alive.load_config(example)

    assert sorted(config) == sorted(keep_alive.PATH_DEFAULTS)
    assert not caplog.records
    with open(example) as f:
        text = f.read()
    assert "scopes" not in text
    assert "accounts" not in text
