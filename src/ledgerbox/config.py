# SPDX-License-Identifier: AGPL-3.0-or-later
"""Data-directory resolution and the runtime guard.

The rule this module enforces: **user financial data never lives inside a git
repository.** ``.gitignore`` is a mitigation; refusing to write is the control.

See ``docs/EXECUTION_PLAN.md`` §9.1.
"""

from __future__ import annotations

import contextlib
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_data_dir

from .fsutil import is_link_like

APP_NAME = "ledgerbox"

ENV_DATA_DIR = "LEDGERBOX_DATA_DIR"
ENV_REAL_FIXTURES = "LEDGERBOX_REAL_FIXTURES"

DB_FILENAME = "ledger.db"
CONFIG_FILENAME = "config.toml"

#: Where the local server binds. Loopback, and there is deliberately no option
#: to change it: this application has no authentication of any kind, so the bind
#: address *is* the access control (``docs/THREAT_MODEL.md``). Anyone who truly
#: wants it on a LAN can put a reverse proxy in front and own that decision
#: explicitly, rather than reach it by mistyping a flag.
#:
#: These live here rather than in :mod:`ledgerbox.api.dependencies` — which
#: re-exports them — so that :mod:`ledgerbox.cli` can name the default port in
#: its ``--help`` without importing FastAPI. The web dependencies are optional,
#: and ``ledgerbox ingest`` has to work on an install that never wanted them.
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8787


class DataDirRefused(SystemExit):
    """The requested data directory is not safe to write user data into.

    Subclasses :class:`SystemExit` deliberately:

    * it must never be swallowed by the per-file ``except Exception`` in the
      ingest pipeline (``SystemExit`` derives from ``BaseException``);
    * at the CLI boundary it exits with a message instead of a traceback.

    It is still a distinct type, so tests can assert on it precisely.
    """


def find_git_marker(path: Path) -> Path | None:
    """Return the first ``.git`` found at *path* or any ancestor, else None.

    ``.git`` is checked with ``exists()`` rather than ``is_dir()`` because in a
    worktree or submodule it is a regular *file* containing a gitdir pointer.
    """
    for parent in [path, *path.parents]:
        marker = parent / ".git"
        try:
            if marker.exists():
                return marker
        except OSError:  # unreadable mount point, permission denied, …
            continue
    return None


def default_data_dir() -> Path:
    """The OS data directory: ``%LOCALAPPDATA%\\ledgerbox`` and friends.

    ``appauthor=False`` keeps Windows from nesting it as
    ``…\\Local\\ledgerbox\\ledgerbox``.
    """
    return Path(user_data_dir(APP_NAME, appauthor=False))


def select_data_dir(override: str | os.PathLike[str] | None = None) -> Path:
    """Where the data directory *would* be. No guard, no side effects.

    Precedence: explicit *override* → ``$LEDGERBOX_DATA_DIR`` → OS data dir.
    """
    if override is None:
        env_value = os.environ.get(ENV_DATA_DIR, "").strip()
        override = env_value or None

    raw = Path(override).expanduser() if override is not None else default_data_dir()
    # resolve() so a symlink pointing into a repository cannot slip past the guard.
    return raw.resolve()


def guard_data_dir(directory: Path) -> None:
    """Refuse a data directory that sits at or under a git repository.

    Note this walks *all* ancestors. If your home directory is itself a repo
    (an accidental ``git init ~`` is common), the OS default data dir is inside
    it and ledgerbox will refuse it — deliberately. Pick another location.
    """
    marker = find_git_marker(directory)
    if marker is None:
        return
    raise DataDirRefused(
        f"拒绝写入 {directory}：该路径位于 git 仓库内（发现 {marker}）。\n"
        f"财务数据不应放在版本控制目录。\n"
        f"请改用仓库之外的位置，例如：\n"
        f"    ledgerbox --data-dir <某个不在 git 仓库里的目录>\n"
        f"或设置环境变量 {ENV_DATA_DIR}。\n"
        f"详见 docs/THREAT_MODEL.md 的 portable 模式说明。"
    )


def resolve_data_dir(
    override: str | os.PathLike[str] | None = None,
    *,
    create: bool = True,
) -> Path:
    """Resolve the user-data directory, refusing anything inside a git repo."""
    directory = select_data_dir(override)
    guard_data_dir(directory)
    if create:
        directory.mkdir(parents=True, exist_ok=True)
    return directory


@dataclass(frozen=True)
class DataPaths:
    """Every path ledgerbox writes to, derived from one root.

    The guard runs in ``__post_init__``, not only in :meth:`resolve`, so there
    is no way to construct a ``DataPaths`` pointing inside a repository — a
    control with a bypass is not a control.
    """

    root: Path

    def __post_init__(self) -> None:
        # Normalize first: find_git_marker on a relative path would only walk
        # up to ".", which would let `DataPaths("data")` slip past the guard.
        object.__setattr__(self, "root", Path(self.root).expanduser().resolve())
        guard_data_dir(self.root)

    @classmethod
    def resolve(
        cls,
        override: str | os.PathLike[str] | None = None,
        *,
        create: bool = True,
    ) -> DataPaths:
        paths = cls(resolve_data_dir(override, create=create))
        if create:
            paths.ensure()
        return paths

    @property
    def db(self) -> Path:
        """SQLite system of record."""
        return self.root / DB_FILENAME

    @property
    def archive(self) -> Path:
        """Bronze layer: immutable, content-addressed originals."""
        return self.root / "archive"

    @property
    def extracted(self) -> Path:
        """Extraction cache; fully rebuildable from :attr:`archive`."""
        return self.root / "extracted"

    @property
    def export(self) -> Path:
        """Plain-text escape hatch (beancount, CSV)."""
        return self.root / "export"

    @property
    def incoming(self) -> Path:
        """Spool for uploads, before they are archived.

        An HTTP upload arrives as a stream and the pipeline needs a path, so the
        bytes have to land somewhere first. That somewhere is inside the data
        directory rather than :func:`tempfile.gettempdir`, for two reasons:

        * the system temp directory is outside everything the guard protects —
          the whole point of :func:`guard_data_dir` is that financial data has a
          designated home, and a statement PDF written to ``%TEMP%`` is the same
          document with none of that;
        * a spool file left behind by a crash is then visible in the one place
          the operator already looks, instead of accumulating invisibly.

        Files here are transient: the uploader deletes each one as soon as
        :mod:`ledgerbox.ingest.archive` has taken its own copy.
        """
        return self.root / "incoming"

    @property
    def config_file(self) -> Path:
        return self.root / CONFIG_FILENAME

    def ensure(self) -> DataPaths:
        for directory in (self.root, self.archive, self.extracted, self.export, self.incoming):
            directory.mkdir(parents=True, exist_ok=True)
        return self

    def sweep_archive_temp(self, *, older_than_seconds: float = 3600.0) -> list[Path]:
        """Delete ``.<name>.<rand>.tmp`` debris left by an interrupted archive write.

        :func:`ledgerbox.ingest.archive._copy_into_place` writes into the shard
        directory and then ``os.replace``s; a crash between the two leaves the
        temp file behind. It is this program's own debris, which is why the
        archive survey reports it separately from things it did not write — but
        nothing was removing it, and the same interruption that creates one also
        creates an unrecorded archive entry, so it appears exactly when someone
        is already reading the output looking for problems.

        Same age threshold and same reasoning as :meth:`sweep_incoming`: a write
        in progress right now must not be deleted out from under itself.

        **Never crosses a link.** This used to use ``rglob``, and ``rglob``
        follows junctions — so with a junction under ``archive/`` this method
        deleted a file that was not in the data directory at all, on an
        unattended server-start path, while the survey that *reports* on the
        archive was refusing to cross that same junction and calling it out.
        The function that only looks was careful and the function that deletes
        was not; that is the wrong way round.

        Same *rule* as :func:`ledgerbox.ingest.pipeline.survey_archive`, not the
        same walk: the survey descends only into real ``<YYYY>/<MM>`` shards,
        because a stray directory should be reported once by its top rather than
        enumerated. This descends into every real directory, because debris left
        by an interrupted write is worth removing wherever it is — and a stray
        directory the survey merely names is not somewhere debris should be left
        to accumulate. The shared, load-bearing half is that neither follows a
        link out of the data directory.
        """
        if not self.archive.is_dir() or is_link_like(self.archive):
            return []
        cutoff = time.time() - older_than_seconds
        removed: list[Path] = []
        stack = [self.archive]
        while stack:
            try:
                children = sorted(stack.pop().iterdir())
            except OSError:
                continue
            for path in children:
                if is_link_like(path):
                    continue
                if path.is_dir():
                    stack.append(path)
                    continue
                if not (path.name.startswith(".") and path.name.endswith(".tmp")):
                    continue
                try:
                    if not path.is_file() or path.stat().st_mtime > cutoff:
                        continue
                    path.chmod(0o600)
                    path.unlink()
                except OSError:
                    continue
                removed.append(path)
        return removed

    def sweep_incoming(self, *, older_than_seconds: float = 3600.0) -> list[Path]:
        """Delete abandoned upload spool files. Returns what was removed.

        An upload deletes its own spool in a ``finally``, so anything left here
        belongs to a process that died mid-request. Those are statement PDFs,
        and leaving them is leaving a second unmanaged copy of a bank statement
        on disk for as long as the data directory exists.

        The age threshold is what makes this safe to call at startup rather than
        only when the directory is known to be idle: a second server on another
        port, sharing this data directory, may have an upload in flight. An hour
        is far longer than any upload of a document that is measured in hundreds
        of kilobytes.
        """
        if not self.incoming.is_dir():
            return []
        cutoff = time.time() - older_than_seconds
        removed: list[Path] = []
        for path in sorted(self.incoming.iterdir()):
            if not path.is_file():
                continue
            try:
                if path.stat().st_mtime > cutoff:
                    continue
                path.unlink()
            except OSError:
                # A file another process is still writing, or one already gone.
                # Neither is a reason to fail to start a server.
                continue
            removed.append(path)
        return removed


def real_fixtures_dir() -> Path | None:
    """Directory of real statements for local regression runs, or None.

    Points *outside* the repository on purpose. Tests must skip — never fail —
    when it is unset, so CI never needs real financial data.
    """
    value = os.environ.get(ENV_REAL_FIXTURES, "").strip()
    if not value:
        return None
    path = Path(value).expanduser()
    return path if path.is_dir() else None


def configure_stdio() -> None:
    """Force UTF-8 on stdout/stderr.

    The predecessor crashed with ``UnicodeEncodeError`` (cp1252) before it had
    read a single PDF, because one path component was Chinese.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        # A redirected or already-closed stream cannot be reconfigured, and
        # failing to set an encoding is never a reason to abort the program.
        with contextlib.suppress(ValueError, OSError):
            reconfigure(encoding="utf-8")
