# SPDX-License-Identifier: AGPL-3.0-or-later
"""M0: the runtime guard, path resolution, and atomic writes.

The guard is the control that keeps financial data out of version control.
It gets tested harder than it looks like it deserves.

Guard tests use the ``git_free_tmp`` fixture rather than ``tmp_path`` so the
only ``.git`` in play is the one the test creates — on a host whose home
directory is itself a repo, ``tmp_path`` would make every one of them pass for
the wrong reason.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from ledgerbox.config import (
    ENV_DATA_DIR,
    DataDirRefused,
    DataPaths,
    default_data_dir,
    find_git_marker,
    resolve_data_dir,
    select_data_dir,
)
from ledgerbox.fsutil import atomic_write_bytes, atomic_write_text, sha256_bytes, sha256_file

REPO_ROOT = Path(__file__).resolve().parents[1]

CJK_NAME = "中文 目录 财务"


# --------------------------------------------------------------------------
# the guard
# --------------------------------------------------------------------------


def test_refuses_directory_that_is_a_repo(git_free_tmp: Path) -> None:
    (git_free_tmp / ".git").mkdir()
    with pytest.raises(DataDirRefused) as excinfo:
        resolve_data_dir(git_free_tmp)
    assert str(git_free_tmp / ".git") in str(excinfo.value)


def test_refuses_directory_inside_a_repo(git_free_tmp: Path) -> None:
    (git_free_tmp / ".git").mkdir()
    nested = git_free_tmp / "a" / "b" / "data"
    with pytest.raises(DataDirRefused):
        resolve_data_dir(nested)
    assert not nested.exists(), "guard must refuse before creating anything"


def test_refuses_when_dot_git_is_a_file(git_free_tmp: Path) -> None:
    """Worktrees and submodules use a .git *file*, not a directory."""
    (git_free_tmp / ".git").write_text("gitdir: /elsewhere/.git/worktrees/wt\n", encoding="utf-8")
    with pytest.raises(DataDirRefused):
        resolve_data_dir(git_free_tmp / "data")


def test_refuses_symlink_pointing_into_a_repo(git_free_tmp: Path) -> None:
    repo = git_free_tmp / "repo"
    (repo / "data").mkdir(parents=True)
    (repo / ".git").mkdir()
    link = git_free_tmp / "link"
    try:
        link.symlink_to(repo / "data", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted on this host")
    with pytest.raises(DataDirRefused):
        resolve_data_dir(link)


def test_refuses_this_repository() -> None:
    if not (REPO_ROOT / ".git").exists():
        pytest.skip("not running from a git checkout")
    with pytest.raises(DataDirRefused):
        resolve_data_dir(REPO_ROOT / "data")


def test_error_message_names_a_way_out(git_free_tmp: Path) -> None:
    (git_free_tmp / ".git").mkdir()
    with pytest.raises(DataDirRefused) as excinfo:
        resolve_data_dir(git_free_tmp)
    message = str(excinfo.value)
    assert "--data-dir" in message
    assert ENV_DATA_DIR in message


def test_refused_is_a_systemexit() -> None:
    """So the per-PDF `except Exception` in the pipeline can never eat it."""
    assert issubclass(DataDirRefused, SystemExit)
    assert not issubclass(DataDirRefused, Exception)


def test_find_git_marker_reports_nearest_marker(git_free_tmp: Path) -> None:
    assert find_git_marker(git_free_tmp) is None
    (git_free_tmp / ".git").mkdir()
    deep = git_free_tmp / "x" / "y"
    deep.mkdir(parents=True)
    assert find_git_marker(deep) == git_free_tmp / ".git"


# --------------------------------------------------------------------------
# resolution
# --------------------------------------------------------------------------


def test_accepts_plain_directory_and_creates_it(git_free_tmp: Path) -> None:
    target = git_free_tmp / "ledgerbox-data"
    resolved = resolve_data_dir(target)
    assert resolved == target.resolve()
    assert resolved.is_dir()


def test_create_false_does_not_touch_disk(git_free_tmp: Path) -> None:
    target = git_free_tmp / "nope"
    resolve_data_dir(target, create=False)
    assert not target.exists()


def test_env_var_is_used_when_no_override(
    git_free_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ENV_DATA_DIR, str(git_free_tmp / "from-env"))
    assert resolve_data_dir() == (git_free_tmp / "from-env").resolve()


def test_explicit_override_beats_env(
    git_free_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ENV_DATA_DIR, str(git_free_tmp / "from-env"))
    assert resolve_data_dir(git_free_tmp / "explicit") == (git_free_tmp / "explicit").resolve()


def test_blank_env_falls_back_to_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_DATA_DIR, "   ")
    assert select_data_dir() == default_data_dir().resolve()


def test_default_data_dir_is_not_nested_twice() -> None:
    """platformdirs would give …/Local/ledgerbox/ledgerbox without appauthor=False."""
    parts = default_data_dir().parts
    assert parts[-1] == "ledgerbox"
    assert parts[-2] != "ledgerbox"


def test_default_data_dir_is_outside_this_repo() -> None:
    assert REPO_ROOT not in default_data_dir().resolve().parents


def test_data_paths_layout(git_free_tmp: Path) -> None:
    paths = DataPaths.resolve(git_free_tmp / "root")
    assert paths.db.name == "ledger.db"
    for directory in (paths.root, paths.archive, paths.extracted, paths.export):
        assert directory.is_dir()
    assert paths.archive.parent == paths.root


def test_data_paths_resolve_is_guarded(git_free_tmp: Path) -> None:
    (git_free_tmp / ".git").mkdir()
    with pytest.raises(DataDirRefused):
        DataPaths.resolve(git_free_tmp / "root")


def test_data_paths_constructor_is_guarded_too(git_free_tmp: Path) -> None:
    """A control with a bypass is not a control: DataPaths(...) must guard."""
    (git_free_tmp / ".git").mkdir()
    with pytest.raises(DataDirRefused):
        DataPaths(git_free_tmp / "root")
    assert not (git_free_tmp / "root").exists()


def test_data_paths_normalizes_relative_root(git_free_tmp: Path, monkeypatch) -> None:
    """Relative paths must be resolved before the guard walks ancestors."""
    (git_free_tmp / ".git").mkdir()
    workdir = git_free_tmp / "nested"
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    with pytest.raises(DataDirRefused):
        DataPaths(Path("data"))


# --------------------------------------------------------------------------
# non-ASCII paths — the predecessor died here, before reading its first PDF
# --------------------------------------------------------------------------


def test_cjk_path_resolves_and_creates(git_free_tmp: Path) -> None:
    paths = DataPaths.resolve(git_free_tmp / CJK_NAME / "数据")
    assert paths.root.is_dir()
    assert CJK_NAME in str(paths.root)


def test_cjk_path_round_trips_content(git_free_tmp: Path) -> None:
    paths = DataPaths.resolve(git_free_tmp / CJK_NAME / "数据")
    target = paths.export / "对账单 2025-06.txt"
    atomic_write_text(target, "存入合计 $9,876.54\n")
    assert target.read_text(encoding="utf-8") == "存入合计 $9,876.54\n"


# --------------------------------------------------------------------------
# atomic writes
# --------------------------------------------------------------------------


def test_atomic_write_creates_parents(tmp_path: Path) -> None:
    target = tmp_path / "a" / "b" / "c.txt"
    atomic_write_text(target, "hello")
    assert target.read_text(encoding="utf-8") == "hello"


def test_atomic_write_replaces_and_leaves_no_temp_files(tmp_path: Path) -> None:
    target = tmp_path / "ledger.ndjson"
    atomic_write_text(target, "old\n")
    atomic_write_text(target, "new\n")
    assert target.read_text(encoding="utf-8") == "new\n"
    assert [p.name for p in tmp_path.iterdir()] == ["ledger.ndjson"]


def test_failed_atomic_write_keeps_old_content_and_cleans_up(tmp_path: Path) -> None:
    target = tmp_path / "ledger.ndjson"
    atomic_write_text(target, "old\n")

    with pytest.raises(TypeError):
        atomic_write_bytes(target, "not bytes")  # type: ignore[arg-type]

    assert target.read_text(encoding="utf-8") == "old\n"
    assert [p.name for p in tmp_path.iterdir()] == ["ledger.ndjson"]


def test_atomic_write_text_normalizes_newlines(tmp_path: Path) -> None:
    target = tmp_path / "x.txt"
    atomic_write_text(target, "a\r\nb\rc\n")
    assert target.read_bytes() == b"a\nb\nc\n"


def test_sha256_file_matches_bytes(tmp_path: Path) -> None:
    payload = "余额 $288.71\n".encode()
    target = tmp_path / "s.bin"
    atomic_write_bytes(target, payload)
    assert sha256_file(target) == sha256_bytes(payload)
    # content addressing: same bytes at two different paths → one address
    other = tmp_path / CJK_NAME / "s.bin"
    atomic_write_bytes(other, payload)
    assert sha256_file(other) == sha256_file(target)


def test_sha256_is_stable_and_lowercase_hex() -> None:
    digest = sha256_bytes(b"")
    assert digest == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    assert re.fullmatch(r"[0-9a-f]{64}", digest)
