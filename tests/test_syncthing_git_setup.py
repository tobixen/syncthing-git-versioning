"""
Integration tests for syncthing-git-setup.

Prerequisites: Syncthing must be running and accessible.  All tests are
skipped automatically when Syncthing is not reachable.

Each test creates a temporary Syncthing folder via the REST API and removes it
on teardown.

Run with:
    pytest tests/test_syncthing_git_setup.py -v

Copyright 2024 Tobias Brox
This Source Code Form is subject to the terms of the Mozilla Public
License, v. 2.0. If a copy of the MPL was not distributed with this
file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""

import json
import os
import subprocess
import urllib.error
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest


SCRIPT_DIR = Path(__file__).resolve().parent.parent
SETUP_SCRIPT = SCRIPT_DIR / "syncthing-git-setup"
VERSIONING_SCRIPT = SCRIPT_DIR / "syncthing-git"

# ---------------------------------------------------------------------------
# Low-level Syncthing API helpers (duplicated intentionally — tests should not
# import the implementation under test)
# ---------------------------------------------------------------------------

def _config_search_paths():
    state_home = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
    config_home = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return [
        state_home / "syncthing/config.xml",
        config_home / "syncthing/config.xml",
        Path("/var/lib/syncthing/.config/syncthing/config.xml"),
    ]


def _find_syncthing_config():
    for path in _config_search_paths():
        if path.exists():
            return path
    return None


def _parse_syncthing_config(config_path):
    root = ET.parse(config_path).getroot()
    gui = root.find("gui")
    address = gui.find("address").text
    if not address.startswith("http"):
        address = "http://" + address
    return gui.find("apikey").text, address


def _api(base_url, api_key, path, method="GET", data=None):
    url = base_url.rstrip("/") + path
    headers = {"X-API-Key": api_key}
    body = None
    if data is not None:
        body = json.dumps(data).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=10) as resp:
        content = resp.read()
        return json.loads(content) if content else None


def _get_folder_config(base_url, api_key, folder_id):
    for f in _api(base_url, api_key, "/rest/config/folders"):
        if f["id"] == folder_id:
            return f
    return None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def syncthing_api():
    """Locate Syncthing and verify it is reachable; skip session if not."""
    config_path = _find_syncthing_config()
    if config_path is None:
        pytest.skip("Syncthing config.xml not found in standard locations")

    try:
        api_key, base_url = _parse_syncthing_config(config_path)
    except Exception as exc:
        pytest.skip(f"Could not parse Syncthing config: {exc}")

    try:
        _api(base_url, api_key, "/rest/system/ping")
    except (urllib.error.URLError, OSError):
        pytest.skip("Syncthing is not running / not reachable")

    return {"api_key": api_key, "base_url": base_url}


@pytest.fixture()
def test_folder(syncthing_api, tmp_path):
    """Create a temporary Syncthing folder; delete it (and its git repo) after the test."""
    api_key = syncthing_api["api_key"]
    base_url = syncthing_api["base_url"]

    folder_id = f"sgv-test-{uuid.uuid4().hex[:8]}"
    sync_dir = tmp_path / "sync"
    sync_dir.mkdir()

    _api(
        base_url,
        api_key,
        "/rest/config/folders",
        method="POST",
        data={
            "id": folder_id,
            "label": f"SGV Integration Test {folder_id}",
            "path": str(sync_dir),
            "type": "sendreceive",
            "rescanIntervalS": 3600,
            "fsWatcherEnabled": False,
        },
    )

    yield {
        "id": folder_id,
        "path": sync_dir,
        "api_key": api_key,
        "base_url": base_url,
    }

    try:
        _api(base_url, api_key, f"/rest/config/folders/{folder_id}", method="DELETE")
    except Exception:
        pass  # best-effort cleanup; don't mask the real test failure


def _setup_args(test_folder, *, extra=None):
    """Build the standard non-interactive argument list for the setup script."""
    args = [
        str(SETUP_SCRIPT),
        "--folder-id", test_folder["id"],
        "--versioning-script", str(VERSIONING_SCRIPT),
        "--api-url", test_folder["base_url"],
        "--api-key", test_folder["api_key"],
        "--yes",
        "--no-skip-git-dir",
    ]
    if extra:
        args.extend(extra)
    return args


# ---------------------------------------------------------------------------
# --list-folders
# ---------------------------------------------------------------------------

def test_list_folders_output(test_folder):
    """--list-folders should print the test folder's id and exit zero."""
    result = subprocess.run(
        [
            str(SETUP_SCRIPT),
            "--list-folders",
            "--api-url", test_folder["base_url"],
            "--api-key", test_folder["api_key"],
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert test_folder["id"] in result.stdout


# ---------------------------------------------------------------------------
# Non-interactive (fully specified via CLI flags)
# ---------------------------------------------------------------------------

def test_noninteractive_creates_git_repo(test_folder):
    subprocess.run(_setup_args(test_folder), check=True)
    assert (test_folder["path"] / ".git").is_dir()


def test_noninteractive_configures_versioning_type(test_folder):
    subprocess.run(_setup_args(test_folder), check=True)
    folder = _get_folder_config(test_folder["base_url"], test_folder["api_key"], test_folder["id"])
    assert folder["versioning"]["type"] == "external"


def test_noninteractive_command_contains_script_and_repo(test_folder):
    subprocess.run(_setup_args(test_folder), check=True)
    folder = _get_folder_config(test_folder["base_url"], test_folder["api_key"], test_folder["id"])
    command = folder["versioning"]["params"]["command"]
    assert str(VERSIONING_SCRIPT) in command
    assert str(test_folder["path"]) in command
    assert "%FOLDER_PATH%" in command
    assert "%FILE_PATH%" in command


def test_noninteractive_reuses_existing_git_repo(test_folder):
    """When the folder already has a git repo initialised, it should be left alone."""
    sync_dir = test_folder["path"]
    subprocess.run(["git", "init", str(sync_dir)], check=True, capture_output=True)
    # Plant a sentinel commit so we can verify it survives
    sentinel = sync_dir / "sentinel.txt"
    sentinel.write_text("keep me")
    subprocess.run(["git", "add", "sentinel.txt"], cwd=sync_dir, check=True)
    subprocess.run(
        ["git", "commit", "-m", "sentinel"],
        cwd=sync_dir, check=True,
        env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
             "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"},
    )

    subprocess.run(_setup_args(test_folder), check=True)
    assert sentinel.exists(), "Setup script must not wipe an existing git repo"


def test_noninteractive_invalid_folder_id_exits_nonzero(test_folder):
    result = subprocess.run(
        [
            str(SETUP_SCRIPT),
            "--folder-id", "no-such-folder-id",
            "--versioning-script", str(VERSIONING_SCRIPT),
            "--api-url", test_folder["base_url"],
            "--api-key", test_folder["api_key"],
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0


def test_noninteractive_bad_versioning_script_exits_nonzero(test_folder):
    """--versioning-script pointing at a non-existent path must exit non-zero."""
    result = subprocess.run(
        [
            str(SETUP_SCRIPT),
            "--folder-id", test_folder["id"],
            "--versioning-script", "/no/such/script",
            "--api-url", test_folder["base_url"],
            "--api-key", test_folder["api_key"],
            "--yes",
            "--no-skip-git-dir",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0


# ---------------------------------------------------------------------------
# Interactive mode — folder selection via stdin
# ---------------------------------------------------------------------------

def test_fully_interactive(test_folder):
    """Run with no CLI options at all; provide folder number via stdin.

    This exercises auto-discovery of config.xml (for API credentials) and of
    the versioning script (found next to the setup script in SCRIPT_DIR).
    """
    # Use our already-open API connection to learn the folder's position in the
    # listing — the script itself will re-discover the API via config.xml.
    folders = _api(test_folder["base_url"], test_folder["api_key"], "/rest/config/folders")
    folder_num = next(
        i + 1 for i, f in enumerate(folders) if f["id"] == test_folder["id"]
    )

    result = subprocess.run(
        [str(SETUP_SCRIPT)],
        input=f"{folder_num}\nn\n",  # folder number, then decline skip-git-dir
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert (test_folder["path"] / ".git").is_dir()
    folder = _get_folder_config(test_folder["base_url"], test_folder["api_key"], test_folder["id"])
    assert folder["versioning"]["type"] == "external"
    # The auto-discovered versioning script should be the one in SCRIPT_DIR
    assert str(VERSIONING_SCRIPT) in folder["versioning"]["params"]["command"]


def test_interactive_invalid_folder_number_retries(test_folder):
    """Invalid folder numbers should prompt again until a valid one is entered."""
    folders = _api(test_folder["base_url"], test_folder["api_key"], "/rest/config/folders")
    folder_num = next(
        i + 1 for i, f in enumerate(folders) if f["id"] == test_folder["id"]
    )

    result = subprocess.run(
        [
            str(SETUP_SCRIPT),
            "--versioning-script", str(VERSIONING_SCRIPT),
            "--api-url", test_folder["base_url"],
            "--api-key", test_folder["api_key"],
            "--yes",
            "--no-skip-git-dir",
        ],
        input=f"abc\n0\n99999\n{folder_num}\n",
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert (test_folder["path"] / ".git").is_dir()


# ---------------------------------------------------------------------------
# Overwrite / --yes behaviour
# ---------------------------------------------------------------------------

def test_overwrite_declined_aborts(test_folder):
    """Second setup without --yes: answering N must leave config unchanged."""
    subprocess.run(_setup_args(test_folder), check=True)
    first_command = _get_folder_config(
        test_folder["base_url"], test_folder["api_key"], test_folder["id"]
    )["versioning"]["params"]["command"]

    result = subprocess.run(
        [
            str(SETUP_SCRIPT),
            "--folder-id", test_folder["id"],
            "--versioning-script", str(VERSIONING_SCRIPT),
            "--api-url", test_folder["base_url"],
            "--api-key", test_folder["api_key"],
            "--no-skip-git-dir",
        ],
        input="N\n",
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "Aborted" in result.stdout

    folder = _get_folder_config(test_folder["base_url"], test_folder["api_key"], test_folder["id"])
    assert folder["versioning"]["params"]["command"] == first_command, \
        "Config should be unchanged after aborting"


def test_overwrite_accepted_updates_config(test_folder):
    """Second setup answering Y should succeed and keep versioning configured."""
    subprocess.run(_setup_args(test_folder), check=True)

    result = subprocess.run(
        [
            str(SETUP_SCRIPT),
            "--folder-id", test_folder["id"],
            "--versioning-script", str(VERSIONING_SCRIPT),
            "--api-url", test_folder["base_url"],
            "--api-key", test_folder["api_key"],
            "--no-skip-git-dir",
        ],
        input="y\n",
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr

    folder = _get_folder_config(test_folder["base_url"], test_folder["api_key"], test_folder["id"])
    assert folder["versioning"]["type"] == "external"


# ---------------------------------------------------------------------------
# Emulated sync — verify the versioning hook actually works after setup
# ---------------------------------------------------------------------------

def _invoke_versioning_hook(folder_config, sync_dir, file_path):
    """Call the versioning hook as Syncthing would, substituting template vars."""
    command = folder_config["versioning"]["params"]["command"]
    resolved = (
        command
        .replace("%FOLDER_PATH%", str(sync_dir))
        .replace("%FILE_PATH%", file_path)
    )
    subprocess.run(resolved.split(), check=True)


def test_emulated_sync_file_appears_in_git(test_folder):
    sync_dir = test_folder["path"]
    subprocess.run(_setup_args(test_folder), check=True)

    (sync_dir / "hello.txt").write_text("hello world")
    folder = _get_folder_config(test_folder["base_url"], test_folder["api_key"], test_folder["id"])
    _invoke_versioning_hook(folder, sync_dir, "hello.txt")

    log = subprocess.run(
        ["git", "log", "--oneline"],
        cwd=sync_dir, capture_output=True, text=True, check=True,
    )
    assert log.stdout.strip(), "Expected at least one commit in the git repo"

    subprocess.run(["git", "reset", "--hard"], cwd=sync_dir, check=True)
    assert (sync_dir / "hello.txt").read_text() == "hello world"


def test_emulated_sync_multiple_changes_create_history(test_folder):
    sync_dir = test_folder["path"]
    subprocess.run(_setup_args(test_folder), check=True)
    folder = _get_folder_config(test_folder["base_url"], test_folder["api_key"], test_folder["id"])

    for content in ("version 1", "version 2", "version 3"):
        (sync_dir / "doc.txt").write_text(content)
        _invoke_versioning_hook(folder, sync_dir, "doc.txt")

    log = subprocess.run(
        ["git", "log", "--oneline"],
        cwd=sync_dir, capture_output=True, text=True, check=True,
    )
    assert len(log.stdout.strip().splitlines()) == 3, \
        "Expected one commit per distinct file version"


def test_emulated_sync_subdirectory_file(test_folder):
    """The versioning hook should handle files in subdirectories."""
    sync_dir = test_folder["path"]
    subprocess.run(_setup_args(test_folder), check=True)

    subdir = sync_dir / "docs" / "notes"
    subdir.mkdir(parents=True)
    (subdir / "readme.txt").write_text("nested content")

    folder = _get_folder_config(test_folder["base_url"], test_folder["api_key"], test_folder["id"])
    _invoke_versioning_hook(folder, sync_dir, "docs/notes/readme.txt")

    subprocess.run(["git", "reset", "--hard"], cwd=sync_dir, check=True)
    assert (sync_dir / "docs" / "notes" / "readme.txt").read_text() == "nested content"


# ---------------------------------------------------------------------------
# Malformed --config XML
# ---------------------------------------------------------------------------

def _run_with_config(test_folder, tmp_path, config_xml: str):
    """Write config_xml to a temp file and run the setup script with --config."""
    config_file = tmp_path / "config.xml"
    config_file.write_text(config_xml)
    return subprocess.run(
        [
            str(SETUP_SCRIPT),
            "--config", str(config_file),
            "--folder-id", test_folder["id"],
            "--versioning-script", str(VERSIONING_SCRIPT),
            "--yes",
            "--no-skip-git-dir",
        ],
        capture_output=True,
        text=True,
    )


def test_malformed_config_missing_gui_exits_nonzero(test_folder, tmp_path):
    result = _run_with_config(test_folder, tmp_path, "<configuration/>")
    assert result.returncode != 0


def test_malformed_config_missing_apikey_exits_nonzero(test_folder, tmp_path):
    result = _run_with_config(
        test_folder, tmp_path,
        "<configuration><gui><address>127.0.0.1:8384</address></gui></configuration>",
    )
    assert result.returncode != 0


def test_malformed_config_missing_address_exits_nonzero(test_folder, tmp_path):
    result = _run_with_config(
        test_folder, tmp_path,
        "<configuration><gui><apikey>somekey</apikey></gui></configuration>",
    )
    assert result.returncode != 0


# ---------------------------------------------------------------------------
# Via `syncthing-git-versioning setup …`
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Same-dir: git repo IS the syncthing folder
# ---------------------------------------------------------------------------

@pytest.fixture()
def test_folder_with_git(syncthing_api, tmp_path):
    """Like test_folder but the sync directory is itself a git repository."""
    api_key = syncthing_api["api_key"]
    base_url = syncthing_api["base_url"]

    folder_id = f"sgv-test-{uuid.uuid4().hex[:8]}"
    sync_dir = tmp_path / "sync"
    sync_dir.mkdir()
    subprocess.run(["git", "init", str(sync_dir)], check=True, capture_output=True)

    _api(
        base_url,
        api_key,
        "/rest/config/folders",
        method="POST",
        data={
            "id": folder_id,
            "label": f"SGV Integration Test {folder_id}",
            "path": str(sync_dir),
            "type": "sendreceive",
            "rescanIntervalS": 3600,
            "fsWatcherEnabled": False,
        },
    )

    yield {
        "id": folder_id,
        "path": sync_dir,
        "api_key": api_key,
        "base_url": base_url,
    }

    try:
        _api(base_url, api_key, f"/rest/config/folders/{folder_id}", method="DELETE")
    except Exception:
        pass


def test_same_dir_repo_is_folder(test_folder_with_git):
    """The git repo configured in the versioning command is always the sync folder itself."""
    result = subprocess.run(
        [
            str(SETUP_SCRIPT),
            "--folder-id", test_folder_with_git["id"],
            "--versioning-script", str(VERSIONING_SCRIPT),
            "--api-url", test_folder_with_git["base_url"],
            "--api-key", test_folder_with_git["api_key"],
            "--no-skip-git-dir",
            "--yes",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    folder = _get_folder_config(
        test_folder_with_git["base_url"], test_folder_with_git["api_key"], test_folder_with_git["id"]
    )
    command = folder["versioning"]["params"]["command"]
    parts = command.split()
    assert parts[1] == str(test_folder_with_git["path"]), \
        f"Expected git-repo arg to be the sync dir itself, got: {parts[1]}"


def test_skip_git_dir_adds_ignore_pattern(test_folder_with_git):
    """--skip-git-dir should add /.git to the folder's Syncthing ignore patterns."""
    result = subprocess.run(
        [
            str(SETUP_SCRIPT),
            "--folder-id", test_folder_with_git["id"],
            "--versioning-script", str(VERSIONING_SCRIPT),
            "--api-url", test_folder_with_git["base_url"],
            "--api-key", test_folder_with_git["api_key"],
            "--skip-git-dir",
            "--yes",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    ignores = _api(
        test_folder_with_git["base_url"],
        test_folder_with_git["api_key"],
        f"/rest/db/ignores?folder={test_folder_with_git['id']}",
    )
    assert "/.git" in ignores["ignore"]


def test_no_skip_git_dir_does_not_add_ignore(test_folder_with_git):
    """--no-skip-git-dir must not add /.git to ignore patterns."""
    result = subprocess.run(
        [
            str(SETUP_SCRIPT),
            "--folder-id", test_folder_with_git["id"],
            "--versioning-script", str(VERSIONING_SCRIPT),
            "--api-url", test_folder_with_git["base_url"],
            "--api-key", test_folder_with_git["api_key"],
            "--no-skip-git-dir",
            "--yes",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    ignores = _api(
        test_folder_with_git["base_url"],
        test_folder_with_git["api_key"],
        f"/rest/db/ignores?folder={test_folder_with_git['id']}",
    )
    assert "/.git" not in (ignores.get("ignore") or [])


def test_skip_git_dir_interactive_prompt_yes(test_folder_with_git):
    """Interactive mode: answering y to the .git skip prompt adds the ignore pattern."""
    result = subprocess.run(
        [
            str(SETUP_SCRIPT),
            "--folder-id", test_folder_with_git["id"],
            "--versioning-script", str(VERSIONING_SCRIPT),
            "--api-url", test_folder_with_git["base_url"],
            "--api-key", test_folder_with_git["api_key"],
            "--yes",
        ],
        input="y\n",  # answer the skip-git-dir prompt
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    ignores = _api(
        test_folder_with_git["base_url"],
        test_folder_with_git["api_key"],
        f"/rest/db/ignores?folder={test_folder_with_git['id']}",
    )
    assert "/.git" in ignores["ignore"]


@pytest.fixture()
def test_folder_subdir_of_git(syncthing_api, tmp_path):
    """Syncthing folder is a subdirectory of an existing git repository."""
    api_key = syncthing_api["api_key"]
    base_url = syncthing_api["base_url"]

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    subprocess.run(["git", "init", str(repo_dir)], check=True, capture_output=True)

    folder_id = f"sgv-test-{uuid.uuid4().hex[:8]}"
    sync_dir = repo_dir / "docs"
    sync_dir.mkdir()

    _api(
        base_url,
        api_key,
        "/rest/config/folders",
        method="POST",
        data={
            "id": folder_id,
            "label": f"SGV Integration Test {folder_id}",
            "path": str(sync_dir),
            "type": "sendreceive",
            "rescanIntervalS": 3600,
            "fsWatcherEnabled": False,
        },
    )

    yield {
        "id": folder_id,
        "path": sync_dir,
        "repo": repo_dir,
        "api_key": api_key,
        "base_url": base_url,
    }

    try:
        _api(base_url, api_key, f"/rest/config/folders/{folder_id}", method="DELETE")
    except Exception:
        pass


def test_subdir_setup_and_sync(test_folder_subdir_of_git):
    """When syncthing_dir is a subdir of an existing git repo, setup initialises git in the
    subdir itself and the versioning hook commits files there."""
    tf = test_folder_subdir_of_git
    result = subprocess.run(
        [
            str(SETUP_SCRIPT),
            "--folder-id", tf["id"],
            "--versioning-script", str(VERSIONING_SCRIPT),
            "--api-url", tf["base_url"],
            "--api-key", tf["api_key"],
            "--yes",
            "--no-skip-git-dir",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr

    folder = _get_folder_config(tf["base_url"], tf["api_key"], tf["id"])
    (tf["path"] / "notes.txt").write_text("hello from docs")
    _invoke_versioning_hook(folder, tf["path"], "notes.txt")

    log = subprocess.run(
        ["git", "log", "--oneline"], cwd=tf["path"], capture_output=True, text=True, check=True,
    )
    assert log.stdout.strip(), "Expected a commit in the git repo"

    subprocess.run(["git", "reset", "--hard"], cwd=tf["path"], check=True)
    assert (tf["path"] / "notes.txt").read_text() == "hello from docs"


# ---------------------------------------------------------------------------
# Via `syncthing-git-versioning setup …`
# ---------------------------------------------------------------------------

def test_via_main_script_setup_subcommand(test_folder):
    """Invoke via `syncthing-git-versioning setup …`."""
    subprocess.run(
        [
            str(VERSIONING_SCRIPT),
            "setup",
            "--folder-id", test_folder["id"],
            "--versioning-script", str(VERSIONING_SCRIPT),
            "--api-url", test_folder["base_url"],
            "--api-key", test_folder["api_key"],
            "--yes",
            "--no-skip-git-dir",
        ],
        check=True,
    )
    assert (test_folder["path"] / ".git").is_dir()
    folder = _get_folder_config(test_folder["base_url"], test_folder["api_key"], test_folder["id"])
    assert folder["versioning"]["type"] == "external"
