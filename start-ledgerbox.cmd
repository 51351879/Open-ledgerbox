@echo off
rem SPDX-License-Identifier: AGPL-3.0-or-later
rem
rem Double-click this to start ledgerbox and open it in a browser.
rem
rem It exists because the CLI lives four directories down and a double-clicked
rem console window closes the instant anything goes wrong, so a failed start and
rem a successful one look identical: an empty desktop. Every exit below pauses,
rem which is the whole point -- the operator gets to read why.
rem
rem The data directory is NOT hardcoded. This file ships in a public repository
rem and one person's disk layout is not a default; more to the point, a runtime
rem guard refuses to write user data anywhere under a `.git`, so the right path
rem is a local fact rather than a shipped one. Order: the environment first,
rem then `data-dir.txt` beside this script (untracked, see .gitignore).
rem
rem This script never reads the path text itself. It used to (`set /p`), and
rem cmd.exe decodes a redirected file in the console's OEM codepage -- the
rem UTF-8 bytes of a Chinese folder name came out as CP437 mojibake, and the
rem server faithfully created and served a directory named that, while setup
rem and the MCP registration pointed at the real one. One machine, two
rem ledgers, no error anywhere. So the path travels either as an environment
rem variable (Unicode end to end, cmd never decodes bytes) or as a *filename*
rem handed to `--data-dir-file`, which ledgerbox reads as UTF-8 itself.

setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\ledgerbox.exe" (
  echo.
  echo   No virtual environment found at  .venv\Scripts\ledgerbox.exe
  echo.
  echo   From this folder, run:   python -m venv .venv
  echo                            .venv\Scripts\pip install -e .
  echo.
  pause
  exit /b 2
)

if defined LEDGERBOX_DATA_DIR goto from_env
if exist "data-dir.txt" goto from_file

echo.
echo   ledgerbox does not know where to keep your data.
echo.
echo   Create a file called  data-dir.txt  next to this script, holding one
echo   line: the folder your statements and ledger should live in. Example:
echo.
echo       C:\ledger-data\my
echo.
echo   Save it as UTF-8. It must not be inside a git repository -- ledgerbox
echo   refuses to write financial data into one, and that refusal is
echo   deliberate.
echo.
pause
exit /b 2

:from_env
echo Starting ledgerbox on http://127.0.0.1:8787
echo Data directory: from the LEDGERBOX_DATA_DIR environment variable
echo.
echo Leave this window open. Closing it stops the server.
echo.
".venv\Scripts\ledgerbox.exe" --data-dir "%LEDGERBOX_DATA_DIR%" serve
goto done

:from_file
echo Starting ledgerbox on http://127.0.0.1:8787
echo Data directory: from data-dir.txt, read by ledgerbox itself as UTF-8
echo.
echo Leave this window open. Closing it stops the server.
echo.
".venv\Scripts\ledgerbox.exe" --data-dir-file "data-dir.txt" serve

:done
rem Exit code 2 is this CLI's "something is wrong" -- most often the port is
rem already taken by a copy that is still running, which is exactly the case a
rem person double-clicking twice needs to be told about rather than left to
rem guess at.
echo.
echo ledgerbox exited with code %errorlevel%.
pause
