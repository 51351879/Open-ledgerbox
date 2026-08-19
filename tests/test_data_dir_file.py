# SPDX-License-Identifier: AGPL-3.0-or-later
"""``--data-dir-file``: the path travels as bytes this program decodes itself.

This exists because the first stranger install with a Chinese folder name
served the wrong directory. ``start-ledgerbox.cmd`` used to read
``data-dir.txt`` with ``set /p``, and cmd.exe decodes a redirected file in the
console's OEM codepage -- on that machine CP437 -- so the UTF-8 bytes of
``D:\\test账本-data`` became ``D:\\testΦ┤ªµ£¼-data``, glyph for glyph what the
page then displayed. The server faithfully created and served a directory
whose name was mojibake of the one the user chose, while setup and the MCP
registration -- whose path never passed through cmd.exe -- pointed at the real
one. One machine, two ledgers, no error anywhere.

The launcher now hands the CLI a *filename* -- ASCII, safe in any codepage --
and the CLI reads the path out of the file as UTF-8 itself. No path text ever
exists inside cmd.exe again, which removes the class rather than the instance.

Everything here drives the real ``main()`` so the tests cover the argparse
wiring, not just a helper.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ledgerbox.cli import main

#: The exact folder name from the first real failure, plus the mojibake CP437
#: makes of its UTF-8 bytes -- asserted absent, because rendering this string
#: anywhere means the bug is back.
CHINESE_DIR = "test账本-data"
MOJIBAKE = "test" + "账本".encode().decode("cp437") + "-data"


def _run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _refused(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str]:
    """Usage errors leave through SystemExit(2), as everywhere in this CLI --
    the guard's own test pins the same convention."""
    with pytest.raises(SystemExit) as excinfo:
        main(list(argv))
    assert excinfo.value.code == 2
    return 2, capsys.readouterr().err


def test_a_chinese_data_dir_survives_the_file_byte_for_byte(
    git_free_tmp: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = git_free_tmp / CHINESE_DIR
    pointer = git_free_tmp / "data-dir.txt"
    pointer.write_bytes(str(target).encode("utf-8"))

    code, out, _ = _run(capsys, "--data-dir-file", str(pointer), "doctor")

    assert code == 0
    assert str(target) in out, "doctor must state the exact directory it resolved"
    assert MOJIBAKE not in out
    assert target.is_dir(), "the resolved directory is the one the user named"
    assert not (git_free_tmp / MOJIBAKE).exists()


def test_a_bom_written_by_a_windows_editor_is_not_part_of_the_path(
    git_free_tmp: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Notepad and friends prepend U+FEFF. A path starting with an invisible
    character resolves to a directory the user can see but never typed.
    """
    target = git_free_tmp / CHINESE_DIR
    pointer = git_free_tmp / "data-dir.txt"
    pointer.write_bytes(b"\xef\xbb\xbf" + str(target).encode("utf-8") + b"\r\n")

    code, out, _ = _run(capsys, "--data-dir-file", str(pointer), "doctor")

    assert code == 0
    assert str(target) in out
    assert target.is_dir()


def test_surrounding_quotes_and_whitespace_are_packaging_not_path(
    git_free_tmp: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A path pasted from PowerShell often keeps its quotes. A literal quote is
    not a legal character in a Windows directory name, so treating it as one
    can only ever produce an error or a wrong directory.
    """
    target = git_free_tmp / CHINESE_DIR
    pointer = git_free_tmp / "data-dir.txt"
    pointer.write_text(f'  "{target}"  \n', encoding="utf-8")

    code, out, _ = _run(capsys, "--data-dir-file", str(pointer), "doctor")

    assert code == 0
    assert str(target) in out
    assert target.is_dir()


def test_both_path_sources_at_once_is_an_error_not_a_precedence_rule(
    git_free_tmp: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Two sources that can disagree about where financial data lives must not
    be resolved by a tie-break nobody will remember. The launcher passes
    exactly one of them, and a human passing both is a human confused.
    """
    pointer = git_free_tmp / "data-dir.txt"
    pointer.write_text(str(git_free_tmp / "a"), encoding="utf-8")

    code, err = _refused(
        capsys,
        "--data-dir",
        str(git_free_tmp / "b"),
        "--data-dir-file",
        str(pointer),
        "doctor",
    )

    assert "--data-dir" in err and "--data-dir-file" in err
    assert not (git_free_tmp / "a").exists()
    assert not (git_free_tmp / "b").exists()


def test_a_missing_pointer_file_names_itself_and_writes_nothing(
    git_free_tmp: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _, err = _refused(capsys, "--data-dir-file", str(git_free_tmp / "nowhere.txt"), "doctor")

    assert "nowhere.txt" in err


def test_an_empty_pointer_file_is_refused_rather_than_defaulted(
    git_free_tmp: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Falling back to the OS default here would serve a different ledger than
    the user configured, silently -- the same outcome as the mojibake, reached
    politely.
    """
    pointer = git_free_tmp / "data-dir.txt"
    pointer.write_text("   \n", encoding="utf-8")

    _, err = _refused(capsys, "--data-dir-file", str(pointer), "doctor")

    assert "empty" in err.lower()


def test_a_file_that_is_not_utf8_is_refused_with_the_reason(
    git_free_tmp: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """UTF-16 is what half of Windows tooling writes when nobody asks. Decoding
    it as UTF-8 'with errors ignored' would fabricate a path; refusing names
    the actual problem.
    """
    pointer = git_free_tmp / "data-dir.txt"
    pointer.write_bytes(str(git_free_tmp / CHINESE_DIR).encode("utf-16"))

    _, err = _refused(capsys, "--data-dir-file", str(pointer), "doctor")

    assert "UTF-8" in err
