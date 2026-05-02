"""
Integration tests for syncthing-git-versioning-setup-folder.

Prerequisites: Syncthing must be running and accessible.  All tests are
skipped automatically when Syncthing is not reachable.

Each test creates a temporary Syncthing folder via the REST API and removes it
on teardown.  Temporary git repositories land under tmp_path (pytest manages
cleanup).

Run with:
    pytest syncthing_git_versioning_setup_folder_test.py -v

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


SCRIPT_DIR = Path(__file__).resolve().parent
SETUP_SCRIPT = SCRIPT_DIR / "syncthing-git-versioning-setup-folder"
VERSIONING_SCRIPT = SCRIPT_DIR / "syncthing-git-versioning"

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


def _setup_args(test_folder, git_repo, *, extra=None):
    """Build the standard non-interactive argument list for the setup script."""
    args = [
        str(SETUP_SCRIPT),
        "--folder-id", test_folder["id"],
        "--git-repo", str(git_repo),
        "--versioning-script", str(VERSIONING_SCRIPT),
        "--api-url", test_folder["base_url"],
        "--api-key", test_folder["api_key"],
        "--yes",
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

def test_noninteractive_creates_git_repo(test_folder, tmp_path):
    git_repo = tmp_path / "versions"
    subprocess.run(_setup_args(test_folder, git_repo), check=True)
    assert (git_repo / ".git").is_dir()


def test_noninteractive_configures_versioning_type(test_folder, tmp_path):
    git_repo = tmp_path / "versions"
    subprocess.run(_setup_args(test_folder, git_repo), check=True)
    folder = _get_folder_config(test_folder["base_url"], test_folder["api_key"], test_folder["id"])
    assert folder["versioning"]["type"] == "external"


def test_noninteractive_command_contains_script_and_repo(test_folder, tmp_path):
    git_repo = tmp_path / "versions"
    subprocess.run(_setup_args(test_folder, git_repo), check=True)
    folder = _get_folder_config(test_folder["base_url"], test_folder["api_key"], test_folder["id"])
    command = folder["versioning"]["params"]["command"]
    assert str(VERSIONING_SCRIPT) in command
    assert str(git_repo) in command
    assert "%FOLDER_PATH%" in command
    assert "%FILE_PATH%" in command


def test_noninteractive_reuses_existing_git_repo(test_folder, tmp_path):
    """When the git repo directory already exists and is initialised, it should be left alone."""
    git_repo = tmp_path / "versions"
    git_repo.mkdir()
    subprocess.run(["git", "init", str(git_repo)], check=True)
    # Plant a sentinel commit so we can verify it survives
    sentinel = git_repo / "sentinel.txt"
    sentinel.write_text("keep me")
    subprocess.run(
        ["git", "add", "sentinel.txt"],
        cwd=git_repo, check=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "sentinel"],
        cwd=git_repo, check=True,
        env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
             "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"},
    )

    subprocess.run(_setup_args(test_folder, git_repo), check=True)
    assert sentinel.exists(), "Setup script must not wipe an existing git repo"


def test_noninteractive_invalid_folder_id_exits_nonzero(test_folder, tmp_path):
    git_repo = tmp_path / "versions"
    result = subprocess.run(
        [
            str(SETUP_SCRIPT),
            "--folder-id", "no-such-folder-id",
            "--git-repo", str(git_repo),
            "--versioning-script", str(VERSIONING_SCRIPT),
            "--api-url", test_folder["base_url"],
            "--api-key", test_folder["api_key"],
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0


def test_noninteractive_bad_versioning_script_exits_nonzero(test_folder, tmp_path):
    """--versioning-script pointing at a non-existent path must exit non-zero."""
    git_repo = tmp_path / "versions"
    result = subprocess.run(
        [
            str(SETUP_SCRIPT),
            "--folder-id", test_folder["id"],
            "--git-repo", str(git_repo),
            "--versioning-script", "/no/such/script",
            "--api-url", test_folder["base_url"],
            "--api-key", test_folder["api_key"],
            "--yes",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0


# ---------------------------------------------------------------------------
# Interactive mode — git repo path prompted via stdin
# ---------------------------------------------------------------------------

def test_interactive_git_repo_prompt(test_folder, tmp_path):
    """Omit --git-repo; provide path via stdin prompt."""
    git_repo = tmp_path / "versions"
    result = subprocess.run(
        [
            str(SETUP_SCRIPT),
            "--folder-id", test_folder["id"],
            "--versioning-script", str(VERSIONING_SCRIPT),
            "--api-url", test_folder["base_url"],
            "--api-key", test_folder["api_key"],
            "--yes",
        ],
        input=str(git_repo) + "\n",
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert (git_repo / ".git").is_dir()
    folder = _get_folder_config(test_folder["base_url"], test_folder["api_key"], test_folder["id"])
    assert folder["versioning"]["type"] == "external"


def test_fully_interactive(test_folder, tmp_path):
    """Run with no CLI options at all; provide folder number and git repo via stdin.

    This exercises auto-discovery of config.xml (for API credentials) and of
    the versioning script (found next to the setup script in SCRIPT_DIR).
    """
    git_repo = tmp_path / "versions"

    # Use our already-open API connection to learn the folder's position in the
    # listing — the script itself will re-discover the API via config.xml.
    folders = _api(test_folder["base_url"], test_folder["api_key"], "/rest/config/folders")
    folder_num = next(
        i + 1 for i, f in enumerate(folders) if f["id"] == test_folder["id"]
    )

    result = subprocess.run(
        [str(SETUP_SCRIPT)],
        input=f"{folder_num}\n{git_repo}\n",
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert (git_repo / ".git").is_dir()
    folder = _get_folder_config(test_folder["base_url"], test_folder["api_key"], test_folder["id"])
    assert folder["versioning"]["type"] == "external"
    # The auto-discovered versioning script should be the one in SCRIPT_DIR
    assert str(VERSIONING_SCRIPT) in folder["versioning"]["params"]["command"]


def test_interactive_invalid_folder_number_retries(test_folder, tmp_path):
    """Invalid folder numbers should prompt again until a valid one is entered."""
    git_repo = tmp_path / "versions"

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
        ],
        input=f"abc\n0\n99999\n{folder_num}\n{git_repo}\n",
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert (git_repo / ".git").is_dir()


# ---------------------------------------------------------------------------
# Overwrite / --yes behaviour
# ---------------------------------------------------------------------------

def test_overwrite_declined_aborts(test_folder, tmp_path):
    """Second setup without --yes: answering N must leave config unchanged."""
    git_repo1 = tmp_path / "versions1"
    git_repo2 = tmp_path / "versions2"

    subprocess.run(_setup_args(test_folder, git_repo1), check=True)

    result = subprocess.run(
        [
            str(SETUP_SCRIPT),
            "--folder-id", test_folder["id"],
            "--git-repo", str(git_repo2),
            "--versioning-script", str(VERSIONING_SCRIPT),
            "--api-url", test_folder["base_url"],
            "--api-key", test_folder["api_key"],
        ],
        input="N\n",
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "Aborted" in result.stdout

    folder = _get_folder_config(test_folder["base_url"], test_folder["api_key"], test_folder["id"])
    assert str(git_repo1) in folder["versioning"]["params"]["command"], \
        "Config should still point at the first repo after aborting"


def test_overwrite_accepted_updates_config(test_folder, tmp_path):
    """Second setup answering Y should update the versioning command."""
    git_repo1 = tmp_path / "versions1"
    git_repo2 = tmp_path / "versions2"

    subprocess.run(_setup_args(test_folder, git_repo1), check=True)

    result = subprocess.run(
        [
            str(SETUP_SCRIPT),
            "--folder-id", test_folder["id"],
            "--git-repo", str(git_repo2),
            "--versioning-script", str(VERSIONING_SCRIPT),
            "--api-url", test_folder["base_url"],
            "--api-key", test_folder["api_key"],
        ],
        input="y\n",
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr

    folder = _get_folder_config(test_folder["base_url"], test_folder["api_key"], test_folder["id"])
    assert str(git_repo2) in folder["versioning"]["params"]["command"]


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


def test_emulated_sync_file_appears_in_git(test_folder, tmp_path):
    git_repo = tmp_path / "versions"
    subprocess.run(_setup_args(test_folder, git_repo), check=True)

    (test_folder["path"] / "hello.txt").write_text("hello world")
    folder = _get_folder_config(test_folder["base_url"], test_folder["api_key"], test_folder["id"])
    _invoke_versioning_hook(folder, test_folder["path"], "hello.txt")

    log = subprocess.run(
        ["git", "log", "--oneline"],
        cwd=git_repo, capture_output=True, text=True, check=True,
    )
    assert log.stdout.strip(), "Expected at least one commit in the git repo"

    subprocess.run(["git", "reset", "--hard"], cwd=git_repo, check=True)
    assert (git_repo / "hello.txt").read_text() == "hello world"


def test_emulated_sync_multiple_changes_create_history(test_folder, tmp_path):
    git_repo = tmp_path / "versions"
    subprocess.run(_setup_args(test_folder, git_repo), check=True)
    folder = _get_folder_config(test_folder["base_url"], test_folder["api_key"], test_folder["id"])

    for content in ("version 1", "version 2", "version 3"):
        (test_folder["path"] / "doc.txt").write_text(content)
        _invoke_versioning_hook(folder, test_folder["path"], "doc.txt")

    log = subprocess.run(
        ["git", "log", "--oneline"],
        cwd=git_repo, capture_output=True, text=True, check=True,
    )
    assert len(log.stdout.strip().splitlines()) == 3, \
        "Expected one commit per distinct file version"


def test_emulated_sync_subdirectory_file(test_folder, tmp_path):
    """The versioning hook should handle files in subdirectories."""
    git_repo = tmp_path / "versions"
    subprocess.run(_setup_args(test_folder, git_repo), check=True)

    subdir = test_folder["path"] / "docs" / "notes"
    subdir.mkdir(parents=True)
    (subdir / "readme.txt").write_text("nested content")

    folder = _get_folder_config(test_folder["base_url"], test_folder["api_key"], test_folder["id"])
    _invoke_versioning_hook(folder, test_folder["path"], "docs/notes/readme.txt")

    subprocess.run(["git", "reset", "--hard"], cwd=git_repo, check=True)
    assert (git_repo / "docs" / "notes" / "readme.txt").read_text() == "nested content"


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
            "--git-repo", str(tmp_path / "versions"),
            "--versioning-script", str(VERSIONING_SCRIPT),
            "--yes",
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

def test_via_main_script_setup_subcommand(test_folder, tmp_path):
    """Invoke via `syncthing-git-versioning setup …` once the dispatch bug is fixed."""
    git_repo = tmp_path / "versions"
    subprocess.run(
        [
            str(VERSIONING_SCRIPT),
            "setup",
            "--folder-id", test_folder["id"],
            "--git-repo", str(git_repo),
            "--versioning-script", str(VERSIONING_SCRIPT),
            "--api-url", test_folder["base_url"],
            "--api-key", test_folder["api_key"],
            "--yes",
        ],
        check=True,
    )
    assert (git_repo / ".git").is_dir()
    folder = _get_folder_config(test_folder["base_url"], test_folder["api_key"], test_folder["id"])
    assert folder["versioning"]["type"] == "external"
