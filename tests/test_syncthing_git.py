# Copyright 2024 Robie Basak
# Copyright 2025 Tobias Brox
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Dependencies: pytest, git, git-annex

import os
import shutil
import subprocess
import types

import pytest


@pytest.fixture
def test_paths(tmp_path):
    """Provide a git-initialised sync dir and a separate working dir for cwd."""
    elements = ["sync", "other"]
    paths = types.SimpleNamespace(**{x: (tmp_path / x) for x in elements})
    for e in elements:
        os.mkdir(getattr(paths, e))
    subprocess.check_call(["git", "init"], cwd=paths.sync)
    return paths


def call_target(test_paths, file_path, env=None):
    subprocess.check_call(
        [
            os.path.join(os.path.dirname(__file__), "..", "syncthing-git"),
            test_paths.sync,
            file_path,
        ],
        cwd=test_paths.other,
        env=env,
    )


git_annex_installed = shutil.which("git-annex") is not None


@pytest.mark.parametrize(["annex"], [(False,), (True,)])
def test_single_file(annex, test_paths):
    if annex:
        if not git_annex_installed:
            pytest.skip("git-annex not installed")
        subprocess.check_call(["git", "annex", "init"], cwd=test_paths.sync)
    (test_paths.sync / "target").write_text("content")
    call_target(test_paths, "target")
    subprocess.check_call(["git", "reset", "--hard"], cwd=test_paths.sync)
    assert (test_paths.sync / "target").read_text() == "content"
    assert (test_paths.sync / "target").is_symlink() == annex


@pytest.mark.parametrize(["annex"], [(False,), (True,)])
def test_no_change(annex, test_paths):
    if annex:
        if not git_annex_installed:
            pytest.skip("git-annex not installed")
        subprocess.check_call(["git", "annex", "init"], cwd=test_paths.sync)
    (test_paths.sync / "target").write_text("content")
    call_target(test_paths, "target")
    target = test_paths.sync / "target"
    if target.is_symlink():
        target.unlink()
    target.write_text("content")
    call_target(test_paths, "target")
    log = subprocess.check_output(
        ["git", "log", "--oneline"], cwd=test_paths.sync, text=True
    )
    assert len(log.strip().splitlines()) == 1, "Unchanged content should not produce a second commit"


def test_subdirectory_file(test_paths):
    subdir = test_paths.sync / "docs" / "notes"
    subdir.mkdir(parents=True)
    (subdir / "readme.txt").write_text("nested content")
    call_target(test_paths, "docs/notes/readme.txt")
    subprocess.check_call(["git", "reset", "--hard"], cwd=test_paths.sync)
    assert (test_paths.sync / "docs" / "notes" / "readme.txt").read_text() == "nested content"


def test_git_commit_failure(test_paths):
    """When git commit fails, the original file must continue to exist."""
    wrap_env = dict(os.environ)
    wrap_env["ORIG_GIT_PATH"] = shutil.which("git")

    git_wrapper_path = test_paths.other / "git"
    git_wrapper_path.write_text(
        """#!/bin/sh
echo "Wrapper called" >&2
if [ "$1" = "commit" ]; then
    echo "Wrapper detected commit subcommand; failing deliberately for test" >&2
    exit 1
fi
echo "Wrapper execing the real git at $ORIG_GIT_PATH" >&2
exec "$ORIG_GIT_PATH" "$@"
"""
    )
    git_wrapper_path.chmod(0o755)
    wrap_env["PATH"] = f"{test_paths.other}:{wrap_env['PATH']}"

    (test_paths.sync / "target").write_text("content")
    with pytest.raises(subprocess.CalledProcessError):
        call_target(test_paths, "target", env=wrap_env)
    assert (test_paths.sync / "target").exists()


def test_git_dir_file_is_rejected(test_paths):
    """Files inside .git/ must be rejected with a non-zero exit and the source file must survive."""
    (test_paths.sync / ".git" / "fake").write_text("fake git file")
    with pytest.raises(subprocess.CalledProcessError):
        call_target(test_paths, ".git/fake")
    assert (test_paths.sync / ".git" / "fake").exists(), \
        "Source file must not be deleted when versioning is refused"
