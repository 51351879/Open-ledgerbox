# SPDX-License-Identifier: AGPL-3.0-or-later
"""P1: the local server, the upload endpoint and the review queue.

Two things this file is really testing, underneath the HTTP:

1. **The upload endpoint is not a second way into the ledger.** It is the same
   pipeline behind a socket. A statement that fails reconciliation over HTTP has
   to be refused exactly as hard as one that fails on the command line, and the
   bytes have to be kept either way.
2. **Resolving a review item books nothing.** The queue is where a refusal is
   recorded, not where it is overridden. The row counts before and after a
   dismissal are the assertion; a comment saying so would not be one.

Everything here runs through ``TestClient``'s in-process transport, so it
exercises the ASGI app rather than a real socket. What the socket is bound to is
covered separately and cheaply, by asserting the constant and the absence of a
flag to change it — the interesting failure there is somebody adding a
``--host``, not uvicorn misbehaving.
"""

from __future__ import annotations

from contextlib import closing
from datetime import date
from pathlib import Path
from typing import get_args

import pytest

fastapi = pytest.importorskip("fastapi", reason="P1 web dependencies are not installed")
pytest.importorskip("httpx", reason="fastapi.testclient needs httpx")

from fastapi.testclient import TestClient  # noqa: E402
from synth import Row, StatementBuilder  # noqa: E402

from ledgerbox.agent import agent_status_to_wire, read_agent_status, triage_to_wire  # noqa: E402
from ledgerbox.agent_center import (  # noqa: E402
    record_session_result,
    start_session,
    update_policy,
)
from ledgerbox.agent_skill_install import SkillInspection, SkillState  # noqa: E402
from ledgerbox.analytics.categorize import assign_categories, default_rules  # noqa: E402
from ledgerbox.api.app import create_app  # noqa: E402
from ledgerbox.api.dependencies import DEFAULT_HOST, DEFAULT_PORT  # noqa: E402
from ledgerbox.api.schemas import MAX_BULK_TRANSACTIONS, TransactionSort  # noqa: E402
from ledgerbox.cli import build_parser  # noqa: E402
from ledgerbox.config import DataPaths  # noqa: E402
from ledgerbox.db import repo  # noqa: E402
from ledgerbox.db.connection import connect, connect_read_only, transaction  # noqa: E402
from ledgerbox.db.migrate import open_ledger  # noqa: E402
from ledgerbox.db.repo import insert_source_file, row_counts  # noqa: E402
from ledgerbox.ingest import archive  # noqa: E402
from ledgerbox.ingest.pipeline import (  # noqa: E402
    ARCHIVE_CHECK_IDS,
    transfer_flags,
    verify_ledger,
)
from ledgerbox.ingest.registry import identify_or_raise  # noqa: E402
from ledgerbox.ledger import posting as posting_builder  # noqa: E402
from ledgerbox.proposals import (  # noqa: E402
    Producer,
    group_id_for,  # noqa: E402
    ledger_revision,
)
from ledgerbox.triage import (  # noqa: E402
    TriageDraft,
    TriageGroup,
    TriageScope,
    validate_triage,
)
from ledgerbox.triage import (  # noqa: E402
    group_id_for as triage_group_id_for,
)

WEB_ROOT = Path(__file__).resolve().parents[1] / "src" / "ledgerbox" / "web"

#: Archivable by its first five bytes, unreadable by everything after them.
#: The pipeline must keep it and refuse it — no real statement required, so
#: these run on CI.
HEADER_ONLY_PDF = b"%PDF-1.7\n% enough to archive, not enough to read\n"


@pytest.fixture
def paths(git_free_tmp: Path) -> DataPaths:
    return DataPaths.resolve(git_free_tmp / "data")


@pytest.fixture
def client(paths: DataPaths):
    with TestClient(create_app(paths)) as test_client:
        yield test_client


def _upload(client, content: bytes, filename: str = "statement.pdf"):
    return client.post("/api/upload", files={"file": (filename, content, "application/pdf")})


def reading(paths: DataPaths):
    """An explicitly owned read handle; double-safe after A1 sealed the old trap.

    ``connect_read_only`` now also closes on its own context-manager exit. This
    helper keeps ``closing`` because these tests predate that factory and its
    spelling makes the ownership boundary obvious; either route releases the
    Windows file handle deterministically.
    """
    return closing(connect_read_only(paths.db))


def test_agent_center_keeps_backend_client_skill_mcp_and_session_states_separate(
    client,
    paths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "ledgerbox.api.routes.agent_center.shutil.which",
        lambda command: "synthetic" if command == "codex" else None,
    )
    monkeypatch.setattr(
        "ledgerbox.api.routes.agent_center.inspect_user_skill",
        lambda selected: SkillInspection(
            selected,
            paths.root / "must-not-leave-the-api",
            "missing",
            None,
            "official-classification-v1",
            ("private-file.md",),
        ),
    )

    response = client.get("/api/agent-center")

    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == 3
    assert body["latest_batch"] is None
    assert body["latest_job"] is None
    assert body["ledgerbox"]["ready_for_proposals"] is True
    assert body["ledgerbox"]["passed_checks"] == body["ledgerbox"]["total_checks"] == 9
    assert body["ledgerbox"]["proposal_schema_version"] == 2
    assert body["ledgerbox"]["data_dir"] == str(paths.root)
    assert body["ledgerbox"]["ledger_label"] == paths.root.name
    assert body["ledgerbox"]["pending_triage_count"] == 0
    assert body["ledgerbox"]["open_review_count"] == 0
    assert body["policy"] == {
        "selected_client": None,
        "application_mode": "automatic",
        "enabled": False,
        "auto_classify_new_imports": True,
    }
    by_client = {item["client"]: item for item in body["clients"]}
    assert by_client["codex"]["installed"] is True
    assert by_client["claude-code"]["installed"] is False
    for item in by_client.values():
        assert item["runner_skill_compatible"] is True
        assert item["personal_skill_state"] == "missing"
        assert "skill_compatible" not in item
        assert item["mcp_bridge_available"] is True
        assert item["mcp_session"] == "not_seen"
        assert item["session_active"] is False
        assert item["last_result"] is None
        assert "path" not in item
        assert set(item) == {
            "client",
            "installed",
            "runner_skill_compatible",
            "personal_skill_state",
            "mcp_bridge_available",
            "mcp_session",
            "session_active",
            "last_seen_at",
            "last_result",
            "result_at",
            "candidate_count",
            "submitted_count",
            "error_code",
        }
    assert "may send" in body["provider_disclosure"]
    assert "agent install-skill --client codex" in body["setup_commands"]["codex"]
    assert body["setup_commands"]["codex"].index("install-skill") < body[
        "setup_commands"
    ]["codex"].index("codex mcp add")
    assert "if ($?) { " in body["setup_commands"]["codex"]
    assert "--client codex" in body["setup_commands"]["codex"]
    assert str(paths.root) in body["setup_commands"]["codex"]
    claude_setup = body["setup_commands"]["claude-code"]
    assert "claude mcp add --scope local ledgerbox " in claude_setup
    assert "-e LEDGERBOX_MCP_CLIENT=claude-code" in claude_setup
    assert "LEDGERBOX_DATA_DIR=" in claude_setup
    assert "add-json" not in claude_setup, "the JSON argument dies at the npm-shim boundary"
    registration = claude_setup[claude_setup.index("claude mcp add") :]
    assert " -- " not in registration and "--client claude-code" not in registration, (
        "Claude 2.1.207 parses child --flags as its own even after a separator"
    )
    assert registration.index("ledgerbox-mcp") < registration.index("-e LEDGERBOX_MCP_CLIENT"), (
        "the variadic -e placed before the command positional swallows it"
    )
    assert "agent install-skill --client claude-code" in body[
        "setup_commands"
    ]["claude-code"]
    assert "--force" not in "\n".join(body["setup_commands"].values())
    assert "--yes" not in "\n".join(body["setup_commands"].values())
    assert body["setup_guide"] == "docs/AGENT_SETUP.md"


@pytest.mark.parametrize("selected", ["codex", "claude-code"])
@pytest.mark.parametrize("personal_state", ["missing", "current", "outdated", "custom"])
def test_agent_center_personal_skill_status_is_aggregate_only(
    client,
    paths: DataPaths,
    monkeypatch: pytest.MonkeyPatch,
    selected: str,
    personal_state: SkillState,
) -> None:
    private_target = paths.root / "private-home" / selected
    monkeypatch.setattr(
        "ledgerbox.api.routes.agent_center.inspect_user_skill",
        lambda requested: SkillInspection(
            requested,
            private_target,
            personal_state,
            "private-installed-version",
            "private-current-version",
            ("private-file.md",),
        ),
    )

    body = client.get("/api/agent-center").json()
    item = next(value for value in body["clients"] if value["client"] == selected)

    assert item["personal_skill_state"] == personal_state
    assert str(private_target) not in str(item)
    assert "private-installed-version" not in str(item)
    assert "private-current-version" not in str(item)
    assert "private-file.md" not in str(item)


@pytest.mark.parametrize("workspace_kind", ["checkout", "package"])
@pytest.mark.parametrize("selected", ["codex", "claude-code"])
def test_agent_center_runner_compatibility_is_independent_of_personal_skill_state(
    client,
    git_free_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
    workspace_kind: str,
    selected: str,
) -> None:
    workspace = git_free_tmp / workspace_kind
    for client_name, relative in {
        "codex": Path(".agents/skills/ledgerbox/SKILL.md"),
        "claude-code": Path(".claude/skills/ledgerbox/SKILL.md"),
    }.items():
        skill = workspace / relative
        skill.parent.mkdir(parents=True, exist_ok=True)
        skill.write_text(
            f"{client_name} proposal_schema_version review_first automatic\n",
            encoding="utf-8",
        )
    contract = workspace / "docs/AGENT_CONTRACT.md"
    contract.parent.mkdir(parents=True, exist_ok=True)
    contract.write_text("proposal_schema_version review_first automatic\n", encoding="utf-8")
    monkeypatch.setattr(
        "ledgerbox.api.routes.agent_center.agent_workspace_root", lambda: workspace
    )
    monkeypatch.setattr(
        "ledgerbox.api.routes.agent_center.inspect_user_skill",
        lambda requested: SkillInspection(
            requested,
            git_free_tmp / "personal-skill",
            "missing",
            None,
            "official-classification-v1",
        ),
    )

    body = client.get("/api/agent-center").json()
    item = next(value for value in body["clients"] if value["client"] == selected)

    assert item["runner_skill_compatible"] is True
    assert item["personal_skill_state"] == "missing"


@pytest.mark.parametrize(
    ("selected", "registration"),
    [("codex", "codex mcp add"), ("claude-code", "claude mcp add --scope local")],
)
def test_agent_center_setup_command_cannot_register_after_a_failed_install(
    client,
    selected: str,
    registration: str,
) -> None:
    command = client.get("/api/agent-center").json()["setup_commands"][selected]

    # A console consumes pasted text one line at a time, so a newline before the guard
    # would let registration run even after the personal Skill installation failed.
    assert "\n" not in command
    install = command.index("agent install-skill")
    guard = command.index("if ($?) { ")
    assert install < guard < command.index(registration)
    success, _, failure = command[guard:].partition(" } else { ")
    assert registration in success
    assert registration not in failure
    assert "install-skill" not in failure
    assert "--force" not in command
    assert "--yes" not in command


def test_agent_center_exposes_latest_job_four_way_accounting(
    client,
    paths: DataPaths,
) -> None:
    conn = connect(paths.db)
    try:
        conn.execute(
            "INSERT INTO source_file "
            "(id, sha256, rel_path, media_type, byte_len, ingested_at) "
            "VALUES ('latest-job-source', 'latest-job-source', '2026/08/latest.pdf', "
            "'application/pdf', 1, '2026-08-10T12:00:00+00:00')"
        )
        conn.execute(
            "INSERT INTO agent_classification_job "
            "(id, trigger_source_file_id, client, application_mode, state, "
            "candidate_count, submitted_count, applied_count, omitted_count, "
            "queued_at, started_at, finished_at) VALUES (?, 'latest-job-source', "
            "'codex', 'automatic', 'partial', 5, 3, 3, 2, ?, ?, ?)",
            (
                "job-" + "5" * 32,
                "2026-08-10T12:00:01+00:00",
                "2026-08-10T12:00:02+00:00",
                "2026-08-10T12:00:03+00:00",
            ),
        )
        conn.execute(
            "UPDATE agent_classification_job SET client_outcome = 'timeout', "
            "client_exit_code = NULL, client_log_excerpt = ?",
            ("COFFEE SHOP 4471 looks like dining\n",),
        )
        conn.commit()
    finally:
        conn.close()

    body = client.get("/api/agent-center").json()

    assert body["latest_job"] == {
        "client": "codex",
        "application_mode": "automatic",
        "state": "partial",
        "candidate_count": 5,
        "submitted_count": 3,
        "applied_count": 3,
        "omitted_count": 2,
        "error_code": None,
        "client_outcome": "timeout",
        "client_exit_code": None,
        "queued_at": "2026-08-10T12:00:01+00:00",
        "started_at": "2026-08-10T12:00:02+00:00",
        "finished_at": "2026-08-10T12:00:03+00:00",
    }
    # The excerpt is the client's own words about real transactions. It stays in
    # the operator's data directory; the browser is never told any of it.
    assert "COFFEE SHOP" not in client.get("/api/agent-center").text
    assert "client_log_excerpt" not in client.get("/api/agent-center").text
    assert body["latest_batch"] == {
        "job_count": 1,
        "state": "partial",
        "candidate_count": 5,
        "submitted_count": 3,
        "applied_count": 3,
        "omitted_count": 2,
        "error_code": None,
        "client_outcome": "timeout",
        "rounds_capped": False,
        "failed_rounds": 0,
        "max_rounds": 25,
        "queued_at": "2026-08-10T12:00:01+00:00",
        "started_at": "2026-08-10T12:00:02+00:00",
        "finished_at": "2026-08-10T12:00:03+00:00",
    }


def test_a_classification_round_cannot_be_asked_for_without_a_connected_agent(
    client,
) -> None:
    refused = client.post("/api/agent-center/classify")

    assert refused.status_code == 409
    assert "Classification settings" in refused.json()["detail"]
    assert client.get("/api/agent-center").json()["latest_batch"] is None


def test_asking_for_a_round_queues_exactly_one_and_starts_no_model(
    client,
    paths: DataPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The route's whole durable effect is a queued row; the runner does the rest."""
    drained: list[object] = []
    monkeypatch.setattr(
        "ledgerbox.api.routes.agent_center.drain_jobs",
        lambda target: drained.append(target),
    )
    enabled = client.put(
        "/api/agent-center/policy",
        json={
            "selected_client": "codex",
            "application_mode": "automatic",
            "enabled": False,
            "auto_classify_new_imports": False,
            "acknowledge_provider_data_policy": False,
        },
    )
    assert enabled.status_code == 200
    conn = connect(paths.db)
    try:
        conn.execute(
            "UPDATE agent_local_policy SET enabled = 1, selected_client = 'codex', "
            "application_mode = 'automatic'"
        )
        conn.commit()
    finally:
        conn.close()

    accepted = client.post("/api/agent-center/classify")

    assert accepted.status_code == 202
    batch = accepted.json()["latest_batch"]
    assert batch["state"] == "queued"
    assert batch["job_count"] == 1
    assert batch["omitted_count"] is None, "a queued round has not left anything behind yet"
    assert drained == [paths]
    # A second ask while one is already queued must not stack another round.
    assert client.post("/api/agent-center/classify").status_code == 409


def test_agent_center_reports_active_and_aggregate_result_states(
    client,
    paths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "ledgerbox.api.routes.agent_center.shutil.which",
        lambda _command: "synthetic",
    )
    conn = connect(paths.db)
    try:
        active_id = start_session(conn, client="codex", now="2026-08-10T12:00:00+00:00")
        record_session_result(
            conn,
            session_id=active_id,
            result_state="partial",
            candidate_count=5,
            submitted_count=3,
            error_code=None,
            now="2026-08-10T12:00:01+00:00",
        )
        failed_id = start_session(
            conn,
            client="claude-code",
            now="2026-08-10T12:00:02+00:00",
        )
        record_session_result(
            conn,
            session_id=failed_id,
            result_state="failed",
            candidate_count=None,
            submitted_count=None,
            error_code="proposal_conflict",
            now="2026-08-10T12:00:03+00:00",
        )
    finally:
        conn.close()

    monkeypatch.setattr(
        "ledgerbox.api.routes.agent_center._utc_now",
        lambda: "2026-08-10T12:00:04+00:00",
    )
    body = client.get("/api/agent-center").json()
    by_client = {item["client"]: item for item in body["clients"]}
    assert by_client["codex"]["session_active"] is True
    assert by_client["codex"]["mcp_session"] == "active"
    assert by_client["codex"]["last_result"] == "partial"
    assert by_client["codex"]["candidate_count"] == 5
    assert by_client["codex"]["submitted_count"] == 3
    assert by_client["codex"]["error_code"] is None
    assert by_client["claude-code"]["last_result"] == "failed"
    assert by_client["claude-code"]["error_code"] == "proposal_conflict"


def test_agent_center_policy_enablement_is_strict_and_zero_write_on_failure(
    client,
    paths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "ledgerbox.api.routes.agent_center.shutil.which",
        lambda _command: None,
    )
    body = {
        "selected_client": "codex",
        "application_mode": "automatic",
        "enabled": True,
        "auto_classify_new_imports": True,
        "acknowledge_provider_data_policy": True,
    }

    unavailable = client.put("/api/agent-center/policy", json=body)
    monkeypatch.setattr(
        "ledgerbox.api.routes.agent_center.shutil.which",
        lambda _command: "synthetic",
    )
    missing_acknowledgement = client.put(
        "/api/agent-center/policy",
        json={**body, "acknowledge_provider_data_policy": False},
    )
    unacknowledged = client.put(
        "/api/agent-center/policy",
        json={**body, "enabled": False, "acknowledge_provider_data_policy": False, "extra": 1},
    )

    assert unavailable.status_code == 409
    assert missing_acknowledgement.status_code == 409
    assert unacknowledged.status_code == 422
    with reading(paths) as conn:
        assert tuple(
            conn.execute(
                "SELECT selected_client, application_mode, enabled, auto_classify_new_imports "
                "FROM agent_local_policy WHERE id = 1"
            ).fetchone()
        ) == (None, "automatic", 0, 1)


@pytest.mark.parametrize("personal_state", ["missing", "outdated", "custom"])
def test_agent_center_never_enables_with_a_non_current_personal_skill(
    client,
    paths: DataPaths,
    monkeypatch: pytest.MonkeyPatch,
    personal_state: SkillState,
) -> None:
    monkeypatch.setattr(
        "ledgerbox.api.routes.agent_center.shutil.which",
        lambda _command: "synthetic",
    )
    monkeypatch.setattr(
        "ledgerbox.api.routes.agent_center.inspect_user_skill",
        lambda selected: SkillInspection(
            selected,
            paths.root / "not-returned",
            personal_state,
            None,
            "official-classification-v1",
        ),
    )

    response = client.put(
        "/api/agent-center/policy",
        json={
            "selected_client": "codex",
            "application_mode": "automatic",
            "enabled": True,
            "auto_classify_new_imports": True,
            "acknowledge_provider_data_policy": True,
        },
    )

    assert response.status_code == 409
    with reading(paths) as conn:
        assert tuple(
            conn.execute(
                "SELECT selected_client, application_mode, enabled, auto_classify_new_imports "
                "FROM agent_local_policy WHERE id = 1"
            ).fetchone()
        ) == (None, "automatic", 0, 1)


def test_agent_center_enables_an_available_client_and_agent_status_negotiates_mode(
    client,
    paths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "ledgerbox.api.routes.agent_center.shutil.which",
        lambda _command: "synthetic",
    )
    monkeypatch.setattr(
        "ledgerbox.api.routes.agent_center.inspect_user_skill",
        lambda selected: SkillInspection(
            selected,
            paths.root / "not-returned",
            "current",
            "official-classification-v1",
            "official-classification-v1",
        ),
    )
    body = {
        "selected_client": "claude-code",
        "application_mode": "automatic",
        "enabled": True,
        "auto_classify_new_imports": True,
        "acknowledge_provider_data_policy": True,
    }

    updated = client.put("/api/agent-center/policy", json=body)

    assert updated.status_code == 200
    assert updated.json() == {
        "selected_client": "claude-code",
        "application_mode": "automatic",
        "enabled": True,
        "auto_classify_new_imports": True,
    }
    with reading(paths) as conn:
        status_body = agent_status_to_wire(read_agent_status(conn, paths))
    assert status_body["local_agent_policy"] == updated.json()


# ---------------------------------------------------------------------------
# the bind address is the access control
# ---------------------------------------------------------------------------


def test_the_server_binds_loopback_and_offers_no_way_to_change_it() -> None:
    assert DEFAULT_HOST == "127.0.0.1"
    assert DEFAULT_PORT == 8787

    flags = {
        option
        for action in build_parser()._subparsers._group_actions[0].choices["serve"]._actions
        for option in action.option_strings
    }
    # There is no authentication anywhere in this application, so a --host flag
    # would put a year of transaction history one typo from the LAN.
    assert "--host" not in flags
    assert "--port" in flags and "--no-browser" in flags


def test_no_cors_middleware_is_installed(paths: DataPaths) -> None:
    app = create_app(paths)
    names = [middleware.cls.__name__ for middleware in app.user_middleware]
    assert "CORSMiddleware" not in names, "same-origin only is the design, not an oversight"


def assert_security_headers(response) -> None:
    """The four headers, asserted from one place.

    Extracted so that a route added later is covered by naming it rather than
    by copying four assertions, which is how one of the copies comes to be the
    weaker one.
    """
    csp = response.headers["content-security-policy"]
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"


@pytest.mark.parametrize(
    ("method", "path", "expected"),
    [
        ("get", "/api/health", 200),
        ("get", "/api/statements", 200),
        ("get", "/static/does-not-exist.css", 404),
        ("post", "/api/upload", 400),
        # The two endpoints that can delete a ledger, on their refusal paths.
        # A header present only on the happy path is missing exactly where the
        # page is rendering something nobody expected.
        ("post", f"/api/statements/{'0' * 64}/deletion-plan", 404),
        ("delete", f"/api/statements/{'0' * 64}", 404),
        # P2 M4. Both new GETs on their 200, and the transaction list on the
        # 422 FastAPI builds before any of this project's code runs — the
        # response most likely to be rendered by a page that asked for
        # something impossible.
        ("get", "/api/transactions", 200),
        ("get", "/api/categories", 200),
        ("get", "/api/transactions?limit=0", 422),
    ],
)
def test_security_headers_are_on_every_response(client, method, path, expected) -> None:
    response = getattr(client, method)(path)
    assert response.status_code == expected
    assert_security_headers(response)


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ({"category_id": None}, 404),
        ({"category_id": "dining"}, 404),
        ({}, 422),
    ],
)
def test_security_headers_are_on_the_write_endpoints_refusals(client, body, expected) -> None:
    """PATCH needs a body, so it cannot ride the table above.

    Both of its refusals are covered: the 404 this application raises and the
    422 FastAPI raises for a body with the required field missing. The header
    assertions are the same function, so the two lists cannot drift apart.
    """
    response = client.patch(f"/api/transactions/{'0' * 64}", json=body)
    assert response.status_code == expected
    assert_security_headers(response)


# ---------------------------------------------------------------------------
# upload: the refusals
# ---------------------------------------------------------------------------


def test_security_headers_survive_an_unhandled_exception(paths: DataPaths) -> None:
    """The one response most likely to be describing a ledger.

    Starlette wraps everything in ``ServerErrorMiddleware``, *outside* any
    middleware the app adds, so a 500 does not pass through the one that stamps
    these. It has to be handled explicitly, and this is the test that says so —
    the gap was real and invisible until it was measured.
    """
    app = create_app(paths)

    @app.get("/api/_boom")
    def boom() -> dict[str, str]:
        raise RuntimeError("deliberate")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/_boom")

    assert response.status_code == 500
    assert "default-src 'self'" in response.headers["content-security-policy"]
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "ledgerbox-data" not in response.text
    assert "Traceback" not in response.text


def test_a_request_with_no_file_part_is_a_bad_request(client) -> None:
    assert client.post("/api/upload").status_code == 400


def test_bytes_that_are_not_a_pdf_are_refused_before_the_pipeline(client, paths) -> None:
    response = _upload(client, b"not a pdf at all, whatever the extension claims")
    assert response.status_code == 415
    assert list(paths.incoming.iterdir()) == [], "the spool must not keep what it rejected"
    assert row_counts_of(paths)["source_file"] == 0


def test_a_pdf_whose_header_sits_behind_a_newline_is_still_a_pdf(client, paths) -> None:
    """A real bank served exactly this, and it was called "not a PDF".

    The file opens in every reader and pdfplumber parses it; the true reason it
    could not be used was that no parser recognises the layout. Refusing it at
    the door replaced a correct diagnosis with a confident wrong one.
    """
    response = _upload(client, b"\n" + HEADER_ONLY_PDF)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "needs_review"
    assert body["review"], "and now it gets the reason it actually deserved"
    assert row_counts_of(paths)["source_file"] == 1, "the bytes are kept, newline included"


def test_a_header_hidden_behind_real_bytes_is_still_refused(client) -> None:
    """Whitespace is not content. Anything else is, and this does not repair."""
    response = _upload(client, b"<html>sorry, please log in</html>" + HEADER_ONLY_PDF)
    assert response.status_code == 415
    assert "not whitespace" in response.json()["detail"]


def test_an_upload_over_the_ceiling_is_refused_and_leaves_nothing_behind(
    paths: DataPaths,
) -> None:
    with TestClient(create_app(paths, max_upload_bytes=2048)) as client:
        response = _upload(client, b"%PDF-1.7\n" + b"x" * 8192)
    assert response.status_code == 413
    assert list(paths.incoming.iterdir()) == []


def test_the_uploaded_filename_never_becomes_a_path(client) -> None:
    response = _upload(client, HEADER_ONLY_PDF, filename="..\\..\\..\\evil name.pdf")
    assert response.status_code == 200
    assert response.json()["filename"] == "evil name.pdf"


# ---------------------------------------------------------------------------
# upload: the gate, over HTTP
# ---------------------------------------------------------------------------


def test_an_unreadable_pdf_is_archived_queued_and_books_nothing(client, paths) -> None:
    body = _upload(client, HEADER_ONLY_PDF).json()

    assert body["status"] == "needs_review"
    assert body["booked"] == 0
    assert body["review"], "a refusal that queues nothing is a refusal nobody can act on"
    assert body["review"][0]["severity"] == "block"

    counts = row_counts_of(paths)
    assert counts["source_file"] == 1, "the bytes are kept so a fixed parser can retry them"
    assert counts["txn"] == 0
    assert counts["txn_identity"] == 0
    assert list(paths.incoming.iterdir()) == [], "the spool is not a second copy of a statement"


def test_a_real_statement_uploads_books_and_reconciles(
    client, paths, real_statements: list[Path]
) -> None:
    body = _upload(client, real_statements[0].read_bytes()).json()

    assert body["status"] == "imported"
    assert body["verdict"] == "ok"
    assert body["booked"] > 0
    assert body["statement_month"]
    assert body["review"] == []
    assert list(paths.incoming.iterdir()) == []

    # No check may *fail*, and every block-level check must actually have run —
    # `verdict == "ok"` already encodes the second half, and this says it in the
    # shape the page renders. Warn-level checks are allowed to skip: Chase does
    # not print a declared transaction count on every statement, and inventing a
    # pass for a check that had no input is the lie the whole project is against.
    assert [c["check_id"] for c in body["checks"] if c["status"] == "fail"] == []
    assert [c["check_id"] for c in body["checks"] if c["severity"] == "block"] != []
    assert all(c["status"] == "pass" for c in body["checks"] if c["severity"] == "block")
    assert "all checks passed" not in body["summary"] or all(
        c["status"] == "pass" for c in body["checks"]
    ), "the summary must not claim more than the checks did"

    counts = row_counts_of(paths)
    assert counts["txn_identity"] == body["booked"]


def test_uploading_the_same_bytes_twice_is_a_no_op(client, paths, real_statements) -> None:
    content = real_statements[0].read_bytes()
    first = _upload(client, content).json()
    before = row_counts_of(paths)

    second = _upload(client, content, filename="downloaded-again.pdf").json()

    assert first["status"] == "imported"
    assert second["status"] == "duplicate"
    assert second["booked"] == 0
    assert row_counts_of(paths) == before, "content addressing makes a re-upload cost nothing"


def test_successful_enabled_upload_schedules_background_classification_once(
    client,
    paths: DataPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = open_ledger(paths.db)
    try:
        update_policy(
            conn,
            selected_client="codex",
            application_mode="automatic",
            enabled=True,
            auto_classify_new_imports=True,
            acknowledge_provider_data_policy=True,
        )
    finally:
        conn.close()
    monkeypatch.setattr(
        "ledgerbox.ingest.pipeline.extract_spans",
        lambda _path: _january(),
    )
    drained: list[DataPaths] = []
    monkeypatch.setattr(
        "ledgerbox.api.routes.upload.drain_jobs",
        lambda target: drained.append(target),
        raising=False,
    )

    response = _upload(
        client,
        b"%PDF-1.7\n% synthetic background classification fixture\n",
    )

    assert response.status_code == 200
    assert response.json()["status"] == "imported"
    assert drained == [paths]


# ---------------------------------------------------------------------------
# review queue
# ---------------------------------------------------------------------------


def _queue(client, **params):
    return client.get("/api/review", params=params).json()


def test_the_queue_is_empty_before_anything_is_uploaded(client) -> None:
    body = _queue(client)
    assert body == {"items": [], "open_block": 0, "open_warn": 0}


def test_a_refusal_shows_up_in_the_queue(client) -> None:
    _upload(client, HEADER_ONLY_PDF)
    body = _queue(client)

    assert body["open_block"] == 1
    item = body["items"][0]
    assert item["severity"] == "block"
    assert item["status"] == "open"
    assert item["message"], "an item with no message is a row nobody can act on"


def test_resolving_an_unknown_item_is_a_not_found(client) -> None:
    response = client.post("/api/review/nope/resolve", json={"action": "resolve"})
    assert response.status_code == 404


def test_dismissing_a_block_item_requires_saying_so_out_loud(client) -> None:
    _upload(client, HEADER_ONLY_PDF)
    item_id = _queue(client)["items"][0]["id"]

    refused = client.post(f"/api/review/{item_id}/resolve", json={"action": "dismiss"})
    assert refused.status_code == 409
    assert "does not book" in refused.json()["detail"]

    accepted = client.post(
        f"/api/review/{item_id}/resolve",
        json={"action": "dismiss", "acknowledge_unbooked": True},
    )
    assert accepted.status_code == 200
    assert accepted.json()["status"] == "dismissed"
    assert accepted.json()["resolved_at"]


def test_a_decision_is_recorded_once(client) -> None:
    _upload(client, HEADER_ONLY_PDF)
    item_id = _queue(client)["items"][0]["id"]

    assert client.post(f"/api/review/{item_id}/resolve", json={"action": "resolve"}).status_code
    again = client.post(f"/api/review/{item_id}/resolve", json={"action": "resolve"})
    assert again.status_code == 409
    assert "already resolved" in again.json()["detail"]


def test_resolving_books_nothing(client, paths) -> None:
    """The whole reason the endpoint is allowed to exist.

    A queue a human can clear is useful. A queue a human can clear *into the
    ledger* would be a gate with a button on it.
    """
    _upload(client, HEADER_ONLY_PDF)
    item_id = _queue(client)["items"][0]["id"]
    before = row_counts_of(paths)

    client.post(
        f"/api/review/{item_id}/resolve",
        json={"action": "dismiss", "acknowledge_unbooked": True, "note": "known bad scan"},
    )

    after = row_counts_of(paths)
    assert {name: count for name, count in after.items() if name != "review_item"} == {
        name: count for name, count in before.items() if name != "review_item"
    }
    assert after["txn"] == 0


def test_dismissing_does_not_hide_the_statement_from_verify(client, paths) -> None:
    from ledgerbox.ingest.pipeline import verify_ledger

    _upload(client, HEADER_ONLY_PDF)
    item_id = _queue(client)["items"][0]["id"]
    client.post(
        f"/api/review/{item_id}/resolve",
        json={"action": "dismiss", "acknowledge_unbooked": True},
    )

    with reading(paths) as conn:
        results = {r.check_id: r for r in verify_ledger(conn, paths)}
    assert results["review_queue"].status == "pass", "the queue really is empty now"
    assert results["unbooked_statements"].status == "fail", "and the statement is still missing"


def test_a_dismissed_statement_can_still_be_re_ingested(client, paths) -> None:
    """The route the dismissal dialog promises, exercised over HTTP.

    Dismissing used to make these exact bytes a permanent no-op, so the advice
    printed in the 409 — fix the parser and re-ingest the archived file — could
    not be followed by the person who had just read it.
    """
    _upload(client, HEADER_ONLY_PDF)
    item_id = _queue(client)["items"][0]["id"]
    client.post(
        f"/api/review/{item_id}/resolve",
        json={"action": "dismiss", "acknowledge_unbooked": True},
    )

    again = _upload(client, HEADER_ONLY_PDF).json()

    assert again["status"] == "needs_review", "a dismissal is a note, not a decision to stop"
    assert again["review"], "and the reasons come back"

    # And they come back saying what the database says. `replace_review_items`
    # deliberately does not resurrect a dismissed item, so reporting these as
    # `open` put two answers to one question on one screen: an upload card
    # listing outstanding reasons above a queue panel saying nothing was waiting.
    assert [item["status"] for item in again["review"]] == ["dismissed"]
    queue = _queue(client)
    assert queue["open_block"] == 0
    assert queue["items"] == []


# ---------------------------------------------------------------------------
# health and statements
# ---------------------------------------------------------------------------


def test_health_describes_an_empty_ledger_without_lying_about_it(client) -> None:
    body = client.get("/api/health").json()
    assert body["database_present"] is True
    assert body["integrity_ok"] is True
    assert body["schema_version"] == body["schema_latest"]
    assert body["open_block"] == 0
    assert body["statement_months"] == 0


# ---------------------------------------------------------------------------
# what the transfer flags cost the headline
# ---------------------------------------------------------------------------


def test_the_totals_contract_reports_the_excluded_amount_not_only_the_count() -> None:
    """Marking a line as a transfer is a subtraction from the headline figures.

    So the wire has to carry how much, not only how many: a count cannot be
    checked against a statement and an amount can. The ``_minor`` suffix is
    asserted rather than assumed because ``schemas``' own module docstring
    calls it load-bearing — it is the whole of what says ``-420000`` is
    −$4,200.00 — and because the page formats by that suffix, not by field.
    """
    from ledgerbox.api.schemas import TotalsOut

    fields = TotalsOut.model_fields
    for name in ("transfer_excluded_in_minor", "transfer_excluded_out_minor"):
        assert name in fields, f"{name} is part of the frozen totals contract"
        assert name.endswith("_minor"), "the suffix is what tells a reader the units"
        assert fields[name].annotation is int, "integer minor units, never a float"
        assert fields[name].default == 0, "nothing flagged is zero, not a missing number"


def test_health_publishes_the_excluded_amounts_where_the_page_looks_for_them(client) -> None:
    """Asserted through the generated document, because the live one can be null.

    ``totals`` is ``null`` until something is booked and booking needs a real
    statement, which CI does not have. Leaving this to the test below would
    leave the contract with no coverage anywhere the fixtures are absent.
    """
    properties = client.get("/openapi.json").json()["components"]["schemas"]["TotalsOut"][
        "properties"
    ]
    assert properties["transfer_excluded_in_minor"]["type"] == "integer"
    assert properties["transfer_excluded_out_minor"]["type"] == "integer"


def test_a_booked_ledger_says_what_the_transfer_flags_removed(client, real_statements) -> None:
    _upload(client, real_statements[0].read_bytes())
    totals = client.get("/api/health").json()["totals"]
    assert totals is not None, "a statement was booked, so there are totals"

    excluded_in = totals["transfer_excluded_in_minor"]
    excluded_out = totals["transfer_excluded_out_minor"]
    assert isinstance(excluded_in, int)
    assert isinstance(excluded_out, int)

    # Same legs and same signs as the two figures they were taken out of, so
    # that `inflow + excluded_in` and `outflow + excluded_out` are the sums that
    # would have been reported had nothing been flagged.
    assert excluded_in >= 0
    assert excluded_out <= 0
    if totals["transfer_count"] == 0:
        assert (excluded_in, excluded_out) == (0, 0), (
            "nothing was flagged, so nothing was held back — a non-zero here is "
            "money missing from In and Out with no transfer to account for it"
        )


def test_statements_lists_what_was_booked(client, real_statements) -> None:
    _upload(client, real_statements[0].read_bytes())
    statements = client.get("/api/statements").json()

    assert len(statements) == 1
    assert statements[0]["txn_count"] > 0
    assert statements[0]["open_block"] == 0
    assert statements[0]["statement_month"]


# ---------------------------------------------------------------------------
# deleting a statement
#
# The product owner's actual situation is the first test here: a refused
# statement sitting in the queue that nothing could remove, keeping `verify` red
# on `unbooked_statements` for as long as the file existed. Everything else in
# this section exists so that the way *out* of the ledger is as hard to use by
# accident as the way in — the 409 that asks again, the 422 that will never
# become a yes, and the plan that measures without writing.
#
# All of it but the last two tests runs without a real statement, because the
# interesting failures here are about rows and refusals rather than about PDFs.
# ---------------------------------------------------------------------------

UNKNOWN_ID = "0" * 64


def _statements(client) -> list[dict]:
    return client.get("/api/statements").json()


def _checks_of(paths: DataPaths) -> dict:
    with reading(paths) as conn:
        return {result.check_id: result for result in verify_ledger(conn, paths)}


def _not_passing(paths: DataPaths) -> list[str]:
    return [
        check_id for check_id, result in _checks_of(paths).items() if result.status != "pass"
    ]


def test_statements_is_empty_before_anything_is_uploaded(client) -> None:
    """The list survived being moved out of ``routes/health.py`` unchanged."""
    assert _statements(client) == []


def test_a_refused_statement_is_listed_with_nothing_booked(client) -> None:
    """The only place ``txn_count = 0`` is visible.

    An archived-and-refused statement has a ``source_file`` row exactly like a
    booked one, and everywhere else in the interface the two look identical.
    """
    body = _upload(client, HEADER_ONLY_PDF).json()
    listed = _statements(client)

    assert len(listed) == 1
    assert listed[0]["source_file_id"] == body["sha256"], "the id is the content hash"
    assert listed[0]["txn_count"] == 0
    assert listed[0]["open_block"] == 1, "and the row says why it is at zero"


def test_a_refused_statement_can_be_deleted_and_verify_goes_green(client, paths) -> None:
    """The case the product owner was stuck on, end to end.

    A statement that was archived and never booked keeps ``unbooked_statements``
    red for as long as its file exists, and dismissing the queue item
    deliberately changes neither (``docs/STATUS.md`` §5.13). Until this endpoint
    there was no supported way to get it out at all.
    """
    _upload(client, HEADER_ONLY_PDF)
    statement_id = _statements(client)[0]["source_file_id"]
    assert _checks_of(paths)["unbooked_statements"].status == "fail", "red before"

    response = client.delete(
        f"/api/statements/{statement_id}", params={"acknowledge_impact": True}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["removed"]["txns"] == 0, "there was nothing booked to remove"
    assert body["removed"]["review_items"] == 1, "the queue item goes with the statement"
    assert body["removed_files"], "and so do the bytes in archive/"
    assert body["unremoved_files"] == []

    assert _statements(client) == []
    assert row_counts_of(paths)["source_file"] == 0
    assert _not_passing(paths) == [], "green after"

    # The summary is allowed to say "all pass" only because every one of them
    # did, and it says how many were run — §5.19 is a whole section about the
    # version of this sentence that does not.
    assert len(body["checks_after"]) == 9, "the archive can be checked once the file is gone"
    assert f"of the {len(body['checks_after'])} checks run afterwards, all pass" in body["summary"]


def test_deleting_without_acknowledging_the_impact_changes_no_rows(client, paths) -> None:
    """The 409 is the server asking the question again, and it costs nothing.

    Same shape as dismissing a block-level review item: accepting a hole in your
    own ledger should be typed out rather than clicked past.
    """
    _upload(client, HEADER_ONLY_PDF)
    statement_id = _statements(client)[0]["source_file_id"]
    before = row_counts_of(paths)
    archived_before = sorted(path.name for path in paths.archive.rglob("*.pdf"))

    response = client.delete(f"/api/statements/{statement_id}")

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert "transaction(s)" in detail, "it names what would be lost, in numbers"
    # Both irreversible kinds, named in the same breath. This statement has
    # neither, so the sentence has to say *that* rather than stay silent: the
    # reader is being asked to accept a loss and is entitled to know there is
    # none. It used to name only the categories and call them "the only" ones,
    # which an acceptance run refuted with a dismissed review item (§5.65).
    assert "hand-set category" in detail
    assert "dismissed review item" in detail
    assert "acknowledge_impact" not in detail, (
        "this sentence is read by a person in a browser with a button in front of "
        "them; the field name belongs in the OpenAPI document"
    )

    assert row_counts_of(paths) == before, "a question is not a deletion"
    assert sorted(path.name for path in paths.archive.rglob("*.pdf")) == archived_before
    assert _statements(client)[0]["source_file_id"] == statement_id


def test_deleting_a_statement_that_is_not_there_is_a_not_found(client) -> None:
    refused = client.delete(
        f"/api/statements/{UNKNOWN_ID}", params={"acknowledge_impact": True}
    )
    assert refused.status_code == 404
    assert client.post(f"/api/statements/{UNKNOWN_ID}/deletion-plan").status_code == 404


def test_the_deletion_plan_measures_without_changing_a_row(client, paths) -> None:
    """A POST that writes nothing — the deletion it performs is rolled back.

    It is a POST because the measurement needs a transaction, and the read-only
    handle every GET holds cannot open one.
    """
    _upload(client, HEADER_ONLY_PDF)
    statement_id = _statements(client)[0]["source_file_id"]
    before = row_counts_of(paths)

    response = client.post(f"/api/statements/{statement_id}/deletion-plan")

    assert response.status_code == 200, response.text
    body = response.json()
    assert row_counts_of(paths) == before, "measuring is not doing"
    assert list(paths.archive.rglob("*.pdf")), "and the file is still there"

    assert body["allowed"] is True
    assert body["refusals"] == []
    assert body["impact"]["review_items"] == 1
    assert body["impact"]["txns"] == 0
    assert body["archive_file_present"] is True

    measured = {check["check_id"] for check in body["checks_after"]}
    assert measured, "a forecast with no checks in it is not a measurement"
    assert [check["status"] for check in body["checks_after"] if check["status"] != "pass"] == []
    assert measured.isdisjoint(ARCHIVE_CHECK_IDS), (
        "the archive checks cannot be measured with the file still on disk"
    )
    for check_id in ARCHIVE_CHECK_IDS:
        assert check_id in body["checks_note"], "and the note says which three, by name"


def test_a_refused_deletion_is_not_a_confirmation_prompt(client, paths) -> None:
    """422, and acknowledging does not help — that is the whole distinction.

    Constructed rather than uploaded: the thirteen real Chase statements do not
    overlap, so the refusal this endpoint exists to report cannot be produced by
    ingesting them. The rows are written straight into the database for the same
    reason ``tests/test_db.py`` writes ``txn_identity`` by hand — to build the
    shape the normal path cannot reach.
    """
    _upload(client, HEADER_ONLY_PDF)
    statement_id = _statements(client)[0]["source_file_id"]

    conn = connect(paths.db)
    try:
        with transaction(conn):
            conn.execute(
                "UPDATE source_file SET period_start = ?, period_end = ? WHERE id = ?",
                ("2025-01-01", "2025-01-31", statement_id),
            )
            insert_source_file(
                conn,
                sha256="b" * 64,
                rel_path="2025/02/" + "b" * 64 + ".pdf",
                media_type="application/pdf",
                byte_len=1024,
                institution="Test Bank",
                period_start="2025-01-15",
                period_end="2025-02-14",
                ingested_at="2025-02-15T00:00:00+00:00",
            )
    finally:
        conn.close()

    plan = client.post(f"/api/statements/{statement_id}/deletion-plan").json()
    assert plan["allowed"] is False
    assert plan["refusals"], "and it says why, in sentences"
    assert plan["checks_after"] == [], (
        "nothing was simulated: a forecast for a deletion that will not be "
        "permitted describes a ledger that will never exist"
    )

    response = client.delete(
        f"/api/statements/{statement_id}", params={"acknowledge_impact": True}
    )

    assert response.status_code == 422, "not 409: no flag turns this into a yes"
    detail = response.json()["detail"]
    assert "overlapping period" in detail
    assert "not a confirmation prompt" in detail, (
        "the browser must not offer a 'do it anyway' button under this"
    )
    assert row_counts_of(paths)["source_file"] == 2, "and nothing was removed"


def test_deleting_a_booked_statement_removes_its_transactions_and_moves_the_totals(
    client, paths, real_statements
) -> None:
    """The numbers go back to exactly what the surviving statement made them.

    Not "the totals changed" — the expectation is measured before the second
    statement is ever uploaded, so it is independent of anything the deletion
    reports about itself.
    """
    if len(real_statements) < 2:
        pytest.skip("needs two real statements to have a before and an after")

    first_id = _upload(client, real_statements[0].read_bytes()).json()["sha256"]
    with_one = client.get("/api/health").json()["totals"]

    second = _upload(client, real_statements[1].read_bytes()).json()
    assert second["status"] == "imported", second["summary"]
    booked = {row["source_file_id"]: row for row in _statements(client)}[second["sha256"]]
    assert booked["txn_count"] > 0

    with_both = client.get("/api/health").json()["totals"]
    assert with_both["txn_count"] == with_one["txn_count"] + booked["txn_count"]

    response = client.delete(
        f"/api/statements/{second['sha256']}", params={"acknowledge_impact": True}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["removed"]["txns"] == booked["txn_count"]
    assert body["removed"]["postings"] == 2 * booked["txn_count"], "double entry, both legs"
    assert body["totals"] == with_one, (
        "deleting the second statement leaves exactly the ledger the first one made"
    )
    surviving = _statements(client)
    assert [row["source_file_id"] for row in surviving] == [first_id]
    assert _not_passing(paths) == [], "and the remaining month still reconciles"


def test_deleting_a_month_in_the_middle_is_allowed_and_said_out_loud_first(
    client, real_statements
) -> None:
    """Taking a month out of the middle leaves later balances irreproducible.

    That is **correct** — the ledger really does have a hole, and a rebuild from
    the remaining archive has the same hole in the same place (``docs/STATUS.md``
    §2.5). What would not be correct is the operator finding out afterwards. So
    the plan measures it, the summary names the check by id, and the 409 says it
    in words before anything is written.

    The middle statement is chosen by ``period_end`` from the API rather than by
    filename order, so this does not quietly depend on how the fixtures are named.
    """
    if len(real_statements) < 3:
        pytest.skip("needs three consecutive statements to have a middle one")

    for path in real_statements[:3]:
        assert _upload(client, path.read_bytes()).json()["status"] == "imported"

    listed = sorted(_statements(client), key=lambda row: row["period_end"])
    assert len(listed) == 3 and all(row["txn_count"] > 0 for row in listed)
    middle = listed[1]["source_file_id"]

    plan = client.post(f"/api/statements/{middle}/deletion-plan").json()

    assert plan["allowed"] is True, "a hole you were told about is not a refusal"
    failing = [check["check_id"] for check in plan["checks_after"] if check["status"] == "fail"]
    assert "balance_assertions" in failing, (
        "the later months' printed balances stop replaying, and that is the point"
    )
    assert "balance_assertions" in plan["summary"], "the one line everybody reads says so"
    assert "all pass" not in plan["summary"]

    refused = client.delete(f"/api/statements/{middle}")
    assert refused.status_code == 409
    assert "balance_assertions" in refused.json()["detail"]


# ---------------------------------------------------------------------------
# P2 M4: the transaction list, its filters, and what a person decides
#
# All of this runs on CI. The ledger is built the way tests/test_forget.py
# builds one: `synth` authors a Chase-shaped Document from coordinates, the real
# parser reads it, `build_entries` turns it into transactions, and the same
# `repo.insert_*` calls `pipeline.ingest_file` makes write them -- including the
# two that write the *rules'* answers, `set_posting_categories` and
# `set_transfer_flags`, because the effective values this endpoint reports are
# composed from those columns and an override folded over them.
#
# Two steps of `ingest_file` are left out: extraction, which needs a PDF, and
# the reconciliation gate, which needs one to have been read.
#
# **The archived bytes are not the statement they stand for.** That is the one
# thing faked here and it is said out loud, as test_forget.py says it: the
# sha256 of those bytes is the statement id every row below hangs from, and it
# has to be a genuine hash of genuine bytes on disk for `verify` to be green
# over this ledger. It is -- all nine checks, asserted by the fixture itself
# before any request is made, because a test that watches a check go red has
# proved nothing if it was already red.
# ---------------------------------------------------------------------------

#: Fixed so the archive shard is nameable. It is the *ingest* date and has
#: nothing to do with either statement's period.
INGESTED_ON = date(2026, 2, 1)
INGESTED_AT = "2026-02-01T00:00:00+00:00"

#: What the two synthetic months contain, pinned as numbers rather than as
#: ``> 0``. ``docs/STATUS.md`` §5.44 is a whole section about the version of
#: these assertions that survives having the data cut out from under it.
JANUARY_ROWS = 9
FEBRUARY_ROWS = 3
ALL_ROWS = JANUARY_ROWS + FEBRUARY_ROWS
DEPOSITS = 2
WITHDRAWALS = ALL_ROWS - DEPOSITS
#: One line the shipped transfer rules claim, and four no category rule claims.
RULE_TRANSFERS = 1
UNCLAIMED = 4

#: Measured on the bank leg: every line as it moved this account, transfers
#: included. Deliberately not the same figures as `/api/health`'s totals.
BANK_IN_MINOR = 202_500
BANK_OUT_MINOR = -35_700
BANK_NET_MINOR = BANK_IN_MINOR + BANK_OUT_MINOR

#: The rule-flagged transfer, and a line no rule claimed at all.
TRANSFER_LINE = "Online Transfer To Savings"
UNCLAIMED_LINE = "TraderXJoes"
GROCERIES_LINE = "Whole Foods"


def _january() -> object:
    """Nine lines, three of them on one day, and the balance chain to prove it.

    The three lines dated 01/06 are the reason this month exists in this shape:
    a paged query ordered by a column with ties is where a missing unique
    tiebreak shows one row twice and never shows another.

    ``Sale 100% Off`` and ``Sale 1000 Off`` are a pair, and so are
    ``Trader_Joes`` and ``TraderXJoes``: in each pair the second row is what a
    search for the first would also return if LIKE's two wildcards were not
    escaped.
    """
    return StatementBuilder(
        period="January 01, 2025 through January 31, 2025",
        beginning="$1,000.00",
        ending="$2,788.00",
        components=(("Deposits and Additions", "2,025.00"), ("Fees", "-237.00")),
        rows=[
            Row("01/05", "Direct Deposit Payroll Acme Widgets", "2,000.00", "3,000.00"),
            Row("01/06", "Whole Foods Market Store", "-50.00", "2,950.00"),
            Row("01/06", "Netflix.Com Membership", "-15.00", "2,935.00"),
            Row("01/06", "Uber Trip Help.Uber.Com", "-20.00", "2,915.00"),
            Row("01/10", "Online Transfer To Savings Account", "-100.00", "2,815.00"),
            Row("01/15", "Monthly Service Fee", "-12.00", "2,803.00"),
            Row("01/20", "Sale 100% Off Widgets Shop", "-30.00", "2,773.00"),
            Row("01/24", "Sale 1000 Off Widgets Shop", "-10.00", "2,763.00"),
            Row("01/28", "Refund From Trader_Joes Market", "25.00", "2,788.00"),
        ],
    ).build()


def _february() -> object:
    """Three lines, so that `month` selects a smaller set than "everything"."""
    return StatementBuilder(
        period="February 01, 2025 through February 28, 2025",
        beginning="$2,788.00",
        ending="$2,668.00",
        components=(("Deposits and Additions", "0.00"), ("Fees", "-120.00")),
        rows=[
            Row("02/03", "Card Purchase 02/02 TraderXJoes Market", "-45.00", "2,743.00"),
            Row("02/11", "Atm Withdrawal Main Street", "-60.00", "2,683.00"),
            Row("02/19", "Netflix.Com Membership", "-15.00", "2,668.00"),
        ],
    ).build()


def _book(conn, paths: DataPaths, document, *, filler: str) -> str:
    """Archive a placeholder original, then book a parsed statement against it."""
    statement = identify_or_raise(document).parse(document)
    entries = posting_builder.build_entries(statement)

    spool = paths.incoming / f"{filler}.pdf"
    spool.write_bytes(b"%PDF-1.7\n% placeholder for the synthetic statement " + filler.encode())
    archived = archive.archive_file(paths, spool, ingested_on=INGESTED_ON)
    spool.unlink()

    with transaction(conn):
        repo.insert_source_file(
            conn,
            sha256=archived.sha256,
            rel_path=archived.rel_path,
            media_type=archived.media_type,
            byte_len=archived.byte_len,
            institution=statement.institution,
            period_start=statement.period_start.isoformat(),
            period_end=statement.period_end.isoformat(),
            ingested_at=INGESTED_AT,
        )
        repo.ensure_account(
            conn,
            account_id=entries.account_id,
            name=entries.account_name,
            kind="asset",
            subtype=entries.subtype,
            currency=entries.currency,
            institution=entries.institution,
            mask=entries.mask,
        )
        # The whole shipped rules file, not a hand-picked pair: `/api/categories`
        # is asserted to return the taxonomy the ledger actually uses, and a
        # fixture that seeded two rows would let that assertion pass while
        # measuring the fixture.
        repo.ensure_categories(conn, rows=list(default_rules().rows()))
        repo.insert_raw_records(
            conn,
            source_file_id=archived.sha256,
            payloads=[(index, "stmttrn", "{}") for index in range(len(statement.transactions))],
            parser_id=statement.parser_id,
            parser_version=statement.parser_version,
        )
        repo.insert_entries(conn, source_file_id=archived.sha256, entries=list(entries.entries))
        repo.set_posting_categories(conn, assignments=assign_categories(entries.entries))
        repo.set_transfer_flags(conn, assignments=transfer_flags(entries.entries))
        repo.upsert_balance_assertions(
            conn, source_file_id=archived.sha256, rows=list(entries.balance_assertions)
        )
        repo.sync_opening_entry(conn, account_id=entries.account_id, currency=entries.currency)

    return str(archived.sha256)


@pytest.fixture
def two_months(client, paths: DataPaths) -> None:
    """January and February, booked into the ledger the running app is serving.

    Depends on ``client`` so that the app's one migration point has already run
    before this opens a second handle to the same file.
    """
    conn = open_ledger(paths.db)
    try:
        _book(conn, paths, _january(), filler="jan")
        _book(conn, paths, _february(), filler="feb")
    finally:
        conn.close()
    assert _not_passing(paths) == [], "the fixture itself has to be a ledger that verifies"


def test_large_flows_lists_only_unconfirmed_answers_biggest_first(
    client, two_months
) -> None:
    """The board exists to put big money in front of a person.

    A line a person already decided directly is confirmed by definition and
    stays off the board; everything else large -- rule answers, learned
    answers, agent answers, and unclassified lines -- queues for one look.
    """
    response = client.get("/api/large-flows", params={"threshold_minor": 1})
    assert response.status_code == 200
    body = response.json()
    assert body["threshold_minor"] == 1
    items = body["items"]
    assert items, "with a one-cent threshold every unconfirmed line qualifies"
    assert all(item["category_decided_by"] != "override" for item in items)
    magnitudes = [abs(int(item["amount_minor"])) for item in items]
    assert magnitudes == sorted(magnitudes, reverse=True), "biggest money first"

    # Confirming one -- re-deciding it with its own current category -- is a
    # direct human decision, so it leaves the board.
    confirmable = next(item for item in items if item["category_id"] is not None)
    patched = client.patch(
        f"/api/transactions/{confirmable['txn_id']}",
        json={"category_id": confirmable["category_id"]},
    )
    assert patched.status_code == 200
    remaining = client.get("/api/large-flows", params={"threshold_minor": 1}).json()["items"]
    assert confirmable["txn_id"] not in {item["txn_id"] for item in remaining}

    assert client.get(
        "/api/large-flows", params={"threshold_minor": 0}
    ).status_code == 422
    assert client.get(
        "/api/large-flows", params={"threshold_minor": "big"}
    ).status_code == 422


def test_large_flows_default_threshold_is_a_thousand_dollars(client, two_months) -> None:
    response = client.get("/api/large-flows")
    assert response.status_code == 200
    body = response.json()
    assert body["threshold_minor"] == 100_000
    assert all(abs(int(item["amount_minor"])) >= 100_000 for item in body["items"])


def _page(client, **params) -> dict:
    response = client.get("/api/transactions", params=params)
    assert response.status_code == 200, response.text
    return dict(response.json())


def _items(client, **params) -> list[dict]:
    return list(_page(client, **params)["items"])


def _only(client, needle: str) -> dict:
    """The single line whose descriptor contains *needle*."""
    found = _items(client, q=needle)
    assert len(found) == 1, f"{needle!r} should name exactly one line, matched {len(found)}"
    return found[0]


def _rule_columns(paths: DataPaths) -> dict[str, list[tuple]]:
    """The two raw columns a PATCH must never touch, read straight from SQL.

    Read through neither view on purpose. ``v_transaction`` reports the
    *effective* category and flag, so a PATCH that wrongly wrote the rules'
    own columns would be invisible in every response this file otherwise reads.
    """
    with reading(paths) as conn:
        return {
            "posting_category": [
                tuple(row)
                for row in conn.execute("SELECT id, category_id FROM posting ORDER BY id")
            ],
            "txn_is_transfer": [
                tuple(row) for row in conn.execute("SELECT id, is_transfer FROM txn ORDER BY id")
            ],
        }


def _overrides(paths: DataPaths) -> list[tuple]:
    with reading(paths) as conn:
        return [
            tuple(row)
            for row in conn.execute(
                "SELECT txn_id, category_id FROM category_override ORDER BY txn_id"
            )
        ]


# --- the empty and absent cases --------------------------------------------


def test_transactions_before_anything_is_ingested_is_an_empty_page(client) -> None:
    body = _page(client)
    assert body["items"] == []
    assert body["totals"] == {
        "matched": 0,
        "bank_in_minor": 0,
        "bank_out_minor": 0,
        "bank_net_minor": 0,
    }
    assert body["summary"], "an empty table still has to say what it means"


def test_transactions_without_a_database_answers_rather_than_failing(client, paths) -> None:
    """Zero rows and no database are the same fact from here — not a 500.

    The file is removed after the app built it, because ``create_app`` migrates
    at startup and there is no other way to reach the branch. That is a real
    state: the data directory is a directory, and nothing stops it being moved
    out from under a running server.
    """
    paths.db.unlink()

    body = _page(client)
    assert body["items"] == []
    assert body["totals"]["matched"] == 0
    assert client.get("/api/categories").json() == []


def test_transactions_with_a_database_is_not_the_empty_answer(client, two_months) -> None:
    """The negative case for the two above: the same call, a ledger behind it."""
    body = _page(client)
    assert len(body["items"]) == ALL_ROWS
    assert body["totals"]["matched"] == ALL_ROWS


# --- the filters, one at a time --------------------------------------------


def test_the_search_matches_a_substring_of_the_banks_own_line(client, two_months) -> None:
    matched = _items(client, q="netflix")
    assert len(matched) == 2, "case-insensitive for ASCII, which is what SQLite's LIKE is"
    assert all("Netflix" in item["raw_descriptor"] for item in matched)
    assert _items(client, q="netflix on mars") == [], "and it matches nothing when nothing matches"


def test_a_percent_sign_in_the_search_is_a_percent_sign(client, two_months) -> None:
    """``100%`` must not be ``100`` followed by "anything".

    The negative case is the row next door: ``Sale 1000 Off Widgets Shop`` is
    what an unescaped ``%`` would also return, and the third assertion shows
    both rows really are reachable — so the second one is measuring the escape
    rather than a typo.
    """
    literal = _items(client, q="100%")
    assert [item["raw_descriptor"] for item in literal] == ["Sale 100% Off Widgets Shop"]
    assert len(_items(client, q="100")) == 2, "both rows contain the digits, so both come back"


def test_an_underscore_in_the_search_is_an_underscore(client, two_months) -> None:
    """The other wildcard, and the one that is easy to forget: ``_`` is any character."""
    literal = _items(client, q="Trader_Joes")
    assert [item["raw_descriptor"] for item in literal] == ["Refund From Trader_Joes Market"]
    assert len(_items(client, q="Trader")) == 2, "TraderXJoes is right there to be over-matched"


def test_month_selects_one_statement_month(client, two_months) -> None:
    january = _items(client, month="2025-01")
    assert len(january) == JANUARY_ROWS
    assert {item["statement_month"] for item in january} == {"2025-01"}
    assert len(_items(client, month="2025-02")) == FEBRUARY_ROWS
    assert _items(client, month="2025-03") == [], "a month with no statement is empty, not an error"


def test_category_selects_the_effective_category(client, two_months) -> None:
    subscriptions = _items(client, category="subscriptions")
    assert len(subscriptions) == 2
    assert {item["category_id"] for item in subscriptions} == {"subscriptions"}
    assert _items(client, category="education") == [], "nothing claimed education"


def test_category_none_selects_the_lines_no_rule_claimed(client, two_months) -> None:
    """``null`` is stored and reported as null, so it needs a filter of its own.

    There is no ``uncategorized`` row to select — ``docs/STATUS.md`` §5.38 —
    which is why this value is a sentinel rather than a category id.
    """
    unclaimed = _items(client, category=repo.NO_CATEGORY)
    assert len(unclaimed) == UNCLAIMED
    assert [item["category_id"] for item in unclaimed] == [None] * UNCLAIMED
    assert {item["category_decided_by"] for item in unclaimed} == {"none"}
    assert len(unclaimed) < ALL_ROWS, "a filter that selects everything selects nothing"


def test_transfer_selects_by_the_effective_flag(client, two_months) -> None:
    flagged = _items(client, transfer=True)
    assert len(flagged) == RULE_TRANSFERS
    assert TRANSFER_LINE in flagged[0]["raw_descriptor"]
    assert flagged[0]["is_transfer"] is True
    assert flagged[0]["transfer_decided_by"] == "rule"

    rest = _items(client, transfer=False)
    assert len(rest) == ALL_ROWS - RULE_TRANSFERS
    assert not any(item["is_transfer"] for item in rest)


def test_direction_selects_by_what_the_line_did_to_the_balance(client, two_months) -> None:
    deposits = _items(client, direction="in")
    assert len(deposits) == DEPOSITS
    assert all(item["amount_minor"] > 0 for item in deposits)

    withdrawals = _items(client, direction="out")
    assert len(withdrawals) == WITHDRAWALS
    assert all(item["amount_minor"] < 0 for item in withdrawals)


def test_two_filters_narrow_further_than_either_one(client, two_months) -> None:
    both = _page(client, q="netflix", month="2025-01")["totals"]["matched"]
    text_only = _page(client, q="netflix")["totals"]["matched"]
    month_only = _page(client, month="2025-01")["totals"]["matched"]

    assert both == 1
    assert both < text_only and both < month_only, "an AND that widens is an OR"


# --- what `totals` describes ------------------------------------------------


def test_the_totals_describe_the_filter_and_not_the_page(client, two_months) -> None:
    """Turning the page must not move the number the pager is reading.

    Both halves are asserted: the totals stay put, *and* the pages really were
    different — a totals block that never moves because the rows never moved
    would prove nothing.
    """
    first = _page(client, limit=5, offset=0)
    second = _page(client, limit=5, offset=5)
    whole = _page(client, limit=50, offset=0)

    assert first["totals"] == second["totals"] == whole["totals"]
    assert first["totals"]["matched"] == ALL_ROWS
    assert len(first["items"]) == 5 < first["totals"]["matched"]
    assert [item["posting_id"] for item in first["items"]] != [
        item["posting_id"] for item in second["items"]
    ], "the two pages have to differ for the equality above to mean anything"


def test_the_totals_agree_with_the_rows_they_describe(client, two_months) -> None:
    body = _page(client, limit=50)
    amounts = [item["amount_minor"] for item in body["items"]]
    assert len(amounts) == body["totals"]["matched"], "the page holds the whole result"

    assert sum(amounts) == body["totals"]["bank_net_minor"] == BANK_NET_MINOR
    assert sum(a for a in amounts if a > 0) == body["totals"]["bank_in_minor"] == BANK_IN_MINOR
    assert sum(a for a in amounts if a < 0) == body["totals"]["bank_out_minor"] == BANK_OUT_MINOR


def test_the_totals_agree_with_the_rows_under_a_filter_too(client, two_months) -> None:
    body = _page(client, month="2025-02", limit=50)
    amounts = [item["amount_minor"] for item in body["items"]]

    assert len(amounts) == FEBRUARY_ROWS
    assert sum(amounts) == body["totals"]["bank_net_minor"] == -12_000
    assert body["totals"]["bank_in_minor"] == 0, "February has no deposit"
    assert body["totals"]["bank_net_minor"] != BANK_NET_MINOR, "the filter really narrowed it"


def test_the_bank_leg_figures_are_not_the_headline_figures(client, two_months) -> None:
    """Two cashflow numbers that look alike cost this project a block-level check.

    ``docs/STATUS.md`` §5.45. The list sums the bank leg with transfers in; the
    header sums the income and expense legs with transfers out. On this ledger
    exactly one line separates them, and the difference is asserted rather than
    described.
    """
    listed = _page(client, limit=50)["totals"]
    headline = client.get("/api/health").json()["totals"]

    assert listed["bank_out_minor"] == BANK_OUT_MINOR
    assert headline["outflow_minor"] == BANK_OUT_MINOR - headline["transfer_excluded_out_minor"]
    assert listed["bank_out_minor"] != headline["outflow_minor"], (
        "one rule-flagged transfer is the whole of the difference, and it is not zero"
    )


# --- paging -----------------------------------------------------------------


def test_two_adjacent_pages_are_disjoint_and_join_up(client, two_months) -> None:
    """Ordered by a day three rows share, which is what a missing tiebreak breaks.

    Ascending on purpose: January's three 01/06 lines then straddle the
    boundary between the two pages, so an ``ORDER BY date`` with no unique last
    key has three rows it may return in any order either side of it.
    """
    first = _items(client, month="2025-01", sort="date", descending=False, limit=3, offset=0)
    second = _items(client, month="2025-01", sort="date", descending=False, limit=3, offset=3)
    whole = _items(client, month="2025-01", sort="date", descending=False, limit=50)

    ids_first = [item["posting_id"] for item in first]
    ids_second = [item["posting_id"] for item in second]

    assert len(ids_first) == len(ids_second) == 3
    assert set(ids_first).isdisjoint(ids_second)
    assert ids_first + ids_second == [item["posting_id"] for item in whole][:6]

    straddled = [item for item in first + second if item["date"] == "2025-01-06"]
    assert len(straddled) == 3, "the tie really does cross the page boundary"
    # And the tie is broken by the statement's own row order, which is what
    # `_TIEBREAK` says it is for. Asserted by name because "the pages are
    # disjoint and join up" holds for *any* deterministic order, including one
    # that shuffles a day's lines into content-hash order in front of a reader.
    assert [item["raw_descriptor"].split()[0] for item in straddled] == [
        "Whole",
        "Netflix.Com",
        "Uber",
    ]


def test_a_page_past_the_end_is_empty_and_says_so(client, two_months) -> None:
    body = _page(client, limit=5, offset=ALL_ROWS + 5)
    assert body["items"] == []
    assert body["totals"]["matched"] == ALL_ROWS, "the count still describes the filter"
    assert "past the end" in body["summary"]


# --- sorting ----------------------------------------------------------------

#: How to read the sorted column back off the wire. ``None`` becomes ``""``
#: because SQLite orders NULL before every value and Python orders ``""``
#: before every non-empty string — one expression, the same order.
SORT_FIELDS = {
    "date": lambda item: item["date"],
    "amount": lambda item: item["amount_minor"],
    "description": lambda item: item["raw_descriptor"],
    "category": lambda item: item["category_id"] or "",
    "month": lambda item: item["statement_month"],
}


def test_the_wire_sort_keys_are_exactly_the_ones_sql_can_order_by() -> None:
    """Two lists of column names, in two files, and they have to be one list.

    ``schemas.TransactionSort`` is what a request is validated against;
    ``repo.SORT_KEYS`` is what the ORDER BY is interpolated from. A key in the
    first and not the second is a 500; a key in the second and not the first is
    a column nobody can sort by. The tests below iterate the same set, so this
    is also what keeps them honest when somebody adds a sixth.
    """
    assert set(get_args(TransactionSort)) == set(repo.SORT_KEYS)
    assert set(SORT_FIELDS) == set(repo.SORT_KEYS), "and this file covers all of them"


@pytest.mark.parametrize("sort", sorted(SORT_FIELDS))
@pytest.mark.parametrize("descending", [True, False])
def test_every_sort_key_orders_by_its_own_column_in_both_directions(
    client, two_months, sort, descending
) -> None:
    body = _page(client, sort=sort, descending=descending, limit=50)
    assert body["sort"] == sort and body["descending"] is descending

    keys = [SORT_FIELDS[sort](item) for item in body["items"]]
    assert len(keys) == ALL_ROWS
    assert keys == sorted(keys, reverse=descending)
    # Every one of these columns has at least two distinct values on this
    # ledger, so the wrong direction is a different list. Without this the
    # assertion above would pass on a column that never varies.
    assert keys != sorted(keys, reverse=not descending)


def test_the_two_directions_return_the_same_rows(client, two_months) -> None:
    up = {item["posting_id"] for item in _items(client, sort="amount", descending=False, limit=50)}
    down = {item["posting_id"] for item in _items(client, sort="amount", descending=True, limit=50)}
    assert up == down and len(up) == ALL_ROWS


# --- the refusals -----------------------------------------------------------

#: Each of these is one step outside what the query can express. Paired below
#: with the value one step inside, so that a 422 for the wrong reason — a typo
#: in a parameter name, say — cannot look like a pass.
REFUSED_QUERIES = [
    ({"sort": "whatever"}, {"sort": "amount"}),
    ({"limit": 0}, {"limit": 1}),
    ({"limit": repo.MAX_PAGE_SIZE + 1}, {"limit": repo.MAX_PAGE_SIZE}),
    ({"offset": -1}, {"offset": 0}),
    ({"month": "2025-1"}, {"month": "2025-01"}),
    ({"direction": "sideways"}, {"direction": "in"}),
]


@pytest.mark.parametrize(("refused", "accepted"), REFUSED_QUERIES)
def test_a_query_the_database_cannot_express_is_refused_before_it_runs(
    client, refused, accepted
) -> None:
    assert client.get("/api/transactions", params=refused).status_code == 422, refused
    assert client.get("/api/transactions", params=accepted).status_code == 200, accepted


# --- the categories a person can choose from --------------------------------


def test_categories_lists_the_shipped_taxonomy_with_its_kinds(client, two_months) -> None:
    listed = client.get("/api/categories").json()
    shipped = {row[0]: row[2] for row in default_rules().rows()}

    assert len(listed) == 24, "the count is pinned; a rules file that shrinks should be noticed"
    assert {item["id"]: item["kind"] for item in listed} == shipped
    assert sorted({item["kind"] for item in listed}) == ["expense", "income", "transfer"]
    assert sum(1 for item in listed if item["kind"] == "transfer") == 2
    assert next(item for item in listed if item["id"] == "investment")["kind"] == "transfer"


def test_categories_is_empty_before_the_first_statement(client) -> None:
    """The rows are written when a statement is booked, not seeded by a migration."""
    assert client.get("/api/categories").json() == []


# --- POST /transactions/category: one decision, many rows -------------------


def _all_ids(client) -> list[str]:
    return [row["txn_id"] for row in _items(client, limit=500)]


def _bulk(client, ids, category_id):
    return client.post(
        "/api/transactions/category", json={"txn_ids": ids, "category_id": category_id}
    )


def test_marking_many_as_transfers_takes_all_of_them_out_of_the_headline(
    client, paths, two_months
) -> None:
    """The feature's whole reason: 79 rows, and until now one click each.

    The rules claim none of the author's real lines, so marking by hand is the
    only thing that makes the breakdown mean anything -- and doing it one row at
    a time is why it had not been done.
    """
    rows = _items(client, limit=500)
    ids = [row["txn_id"] for row in rows]
    # This fixture already contains one line the *rules* flagged, which is the
    # shape §5.69 exists to keep straight: a rule-flagged transfer carries a
    # NULL category, a person-marked one carries the transfer category. It was
    # already out of the figures, so marking it adds no transition -- and
    # expecting otherwise was this test's first draft being wrong about its own
    # fixture rather than the endpoint being wrong about the ledger.
    already = [row for row in rows if row["is_transfer"]]
    assert len(already) == 1, "the fixture's rule-flagged line, which this test leans on"

    before = client.get("/api/analytics").json()["totals"]

    body = _bulk(client, ids, "transfer").json()

    assert body["requested"] == len(ids)
    assert body["changed"] == len(ids), "every line gains an override row, flagged or not"
    assert body["transfer_added"] == len(ids) - len(already)
    assert body["transfer_removed"] == 0

    after = client.get("/api/analytics").json()["totals"]
    assert after["txn_count"] == 0, "every line is a transfer now"
    assert after["inflow_minor"] == 0 and after["outflow_minor"] == 0
    # And the money is accounted for rather than gone: what left the figures is
    # reported beside them, which is the whole point of `transfer_excluded_*`.
    # The identity has to include what was already excluded before, or it would
    # be short by exactly the rule-flagged line.
    assert after["transfer_excluded_in_minor"] == (
        before["inflow_minor"] + before["transfer_excluded_in_minor"]
    )
    assert after["transfer_excluded_out_minor"] == (
        before["outflow_minor"] + before["transfer_excluded_out_minor"]
    )


def test_one_unknown_id_refuses_the_whole_list_and_writes_nothing(
    client, paths, two_months
) -> None:
    """A caller holding a stale id is holding a stale list.

    Writing the part that still resolves would answer a question nobody asked,
    and would do it silently: the person selected a set and would get a
    different one. All of it lands or none of it does.
    """
    ids = _all_ids(client)
    assert len(ids) > 3

    refused = _bulk(client, [ids[0], "0" * 64, ids[1]], "transfer")

    assert refused.status_code == 404
    assert "0" * 64 in refused.json()["detail"]
    assert "Nothing was written" in refused.json()["detail"]
    assert _overrides(paths) == [], "not even the ids that did resolve"


def test_an_unknown_category_refuses_the_whole_list_and_writes_nothing(
    client, paths, two_months
) -> None:
    refused = _bulk(client, _all_ids(client), "not-a-category")

    assert refused.status_code == 422
    assert "not-a-category" in refused.json()["detail"]
    assert _overrides(paths) == []


def test_replacing_a_decision_somebody_made_by_hand_is_counted_on_its_own(
    client, paths, two_months
) -> None:
    """The one part of this that repeating the call cannot undo.

    Withdrawing an override lets the *rules* answer again; it does not restore
    the category a person had chosen before this overwrote it. So it is counted
    separately from `changed` and named in the sentence, the way `forget` names
    what it destroys rather than folding it into a total.
    """
    ids = _all_ids(client)
    client.patch(f"/api/transactions/{ids[0]}", json={"category_id": "dining"})
    client.patch(f"/api/transactions/{ids[1]}", json={"category_id": "transfer"})

    body = _bulk(client, ids, "transfer").json()

    assert body["replaced"] == 1, "only the one that already named a *different* category"
    assert "you had set by hand" in body["summary"]
    assert "dining" not in _dict(_overrides(paths)).values(), "the old decision is gone"


def _dict(pairs):
    return dict(pairs)


def test_withdrawing_many_decisions_hands_them_back_to_the_rules(
    client, paths, two_months
) -> None:
    rows = _items(client, limit=500)
    ids = [row["txn_id"] for row in rows]
    ruled = [row for row in rows if row["is_transfer"]]

    _bulk(client, ids, "transfer")
    assert client.get("/api/analytics").json()["totals"]["txn_count"] == 0

    body = _bulk(client, ids, None).json()

    # Withdrawing hands the line back to the *rules*, and for the one line the
    # rules already flagged that means it stays a transfer. "Undo" here is
    # "stop overruling", not "make it not a transfer".
    assert body["transfer_removed"] == len(ids) - len(ruled)
    assert body["replaced"] == len(ids), "every one of them carried a decision, and it is gone"
    assert _overrides(paths) == []
    assert client.get("/api/analytics").json()["totals"]["txn_count"] == len(ids) - len(ruled)


def test_saying_the_same_thing_twice_changes_nothing_the_second_time(
    client, paths, two_months
) -> None:
    """`unchanged` is the evidence the second click landed and did nothing."""
    ids = _all_ids(client)
    first = _bulk(client, ids, "transfer").json()
    second = _bulk(client, ids, "transfer").json()

    assert first["changed"] == len(ids) and first["unchanged"] == 0
    assert second["changed"] == 0 and second["unchanged"] == len(ids)
    assert second["replaced"] == 0, "the same category is not a decision replaced"
    assert second["transfer_added"] == 0


def test_an_empty_selection_is_refused_rather_than_answered_with_zeroes(
    client, two_months
) -> None:
    """A request naming nothing is a mistake, not an operation over an empty set."""
    assert _bulk(client, [], "transfer").status_code == 422


def test_more_ids_than_one_read_could_have_produced_are_refused(client, two_months) -> None:
    """The ceiling is the page size, so a client cannot name rows it never saw."""
    too_many = [f"{index:064d}" for index in range(MAX_BULK_TRANSACTIONS + 1)]
    assert _bulk(client, too_many, "transfer").status_code == 422


def test_bulk_category_refuses_filter_shaped_extra_fields(client, paths, two_months) -> None:
    """A caller must not be told a filter participated when it was ignored.

    The endpoint's safety property is that it writes only an explicit set of ids
    somebody already saw.  Silently accepting a ``filter`` sibling gives an
    Agent-shaped caller a 200 for a request whose apparent selection semantics
    the server never evaluated.
    """
    ids = _all_ids(client)

    refused = client.post(
        "/api/transactions/category",
        json={
            "txn_ids": [ids[0]],
            "category_id": "transfer",
            "filter": {"direction": "out"},
        },
    )

    assert refused.status_code == 422
    assert _overrides(paths) == []


def test_bulk_category_refuses_duplicate_transaction_ids(client, paths, two_months) -> None:
    """Requested and changed counts describe transactions, not list positions."""
    txn_id = _all_ids(client)[0]

    refused = _bulk(client, [txn_id, txn_id], "transfer")

    assert refused.status_code == 422
    assert _overrides(paths) == []


# --- A1: proposal audit is separate from effective category writes ---------


def _eligible_proposal_ids(client, count: int = 2) -> list[str]:
    rows = client.get("/api/transactions", params={"limit": 500}).json()["items"]
    ids = [row["txn_id"] for row in rows if row["category_decided_by"] == "none"]
    assert len(ids) >= count
    return ids[:count]


def _proposal_body(client, txn_ids: list[str], category_id: str = "dining") -> dict:
    status = client.get("/api/agent-proposals/status").json()
    assert status["schema_version"] == 2
    revision = status["ledger_revision"]
    return {
        "schema_version": 1,
        "ledger_revision": revision,
        "producer": {"client": "codex", "client_version": "synthetic"},
        "groups": [{
            "group_id": group_id_for(category_id, tuple(txn_ids)),
            "category_id": category_id,
            "txn_ids": txn_ids,
        }],
    }


def test_v2_automatic_http_boundary_is_readable_without_breaking_v1(
    client, paths, two_months
) -> None:
    ids = _eligible_proposal_ids(client)
    body = _proposal_body(client, ids[:1], "dining")
    body["schema_version"] = 2
    body["application_mode"] = "automatic"

    submitted = client.post("/api/agent-proposals", json=body)

    assert submitted.status_code == 201
    run_id = submitted.json()["run_id"]
    run = client.get(f"/api/agent-proposals/{run_id}")
    assert run.status_code == 200
    assert (run.json()["schema_version"], run.json()["application_mode"], run.json()["state"]) == (
        2,
        "automatic",
        "completed",
    )
    with reading(paths) as conn:
        override = conn.execute(
            "SELECT category_id, source, agent_run_id FROM category_override WHERE txn_id = ?",
            (ids[0],),
        ).fetchone()
        assert tuple(override) == ("dining", "agent", run_id)


@pytest.mark.parametrize(
    "schema_version,application_mode",
    [(1, "automatic"), (2, None), (2, "review-frist"), (2, 1)],
)
def test_http_proposal_versions_fail_closed(
    client,
    paths,
    two_months,
    schema_version,
    application_mode,
) -> None:
    body = _proposal_body(client, _eligible_proposal_ids(client, 1))
    body["schema_version"] = schema_version
    if application_mode is not None:
        body["application_mode"] = application_mode

    assert client.post("/api/agent-proposals", json=body).status_code == 422
    with reading(paths) as conn:
        assert conn.execute("SELECT COUNT(*) FROM agent_proposal_run").fetchone()[0] == 0


def test_http_v1_explicit_null_v2_field_fails_closed(client, paths, two_months) -> None:
    body = _proposal_body(client, _eligible_proposal_ids(client, 1))
    body["application_mode"] = None

    assert client.post("/api/agent-proposals", json=body).status_code == 422
    with reading(paths) as conn:
        assert conn.execute("SELECT COUNT(*) FROM agent_proposal_run").fetchone()[0] == 0


def test_proposal_submit_is_audit_only_then_review_and_withdraw_are_explicit(
    client, paths, two_months
) -> None:
    ids = _eligible_proposal_ids(client)
    body = _proposal_body(client, ids)

    submitted = client.post("/api/agent-proposals", json=body)

    assert submitted.status_code == 201
    run_id = submitted.json()["run_id"]
    assert submitted.json()["created"] is True
    assert _overrides(paths) == [], "submission did not silently classify anything"
    run = client.get(f"/api/agent-proposals/{run_id}").json()
    assert run["state"] == "open"
    assert {row["outcome"] for row in run["proposals"]} == {"pending"}

    reviewed = client.post(
        f"/api/agent-proposals/{run_id}/review",
        json={"action": "accept", "txn_ids": [ids[0]]},
    )
    assert reviewed.status_code == 200
    assert reviewed.json()["accepted"] == 1
    assert _overrides(paths) == [(ids[0], "dining")]

    withdrawn = client.post(f"/api/agent-proposals/{run_id}/withdraw")
    assert withdrawn.status_code == 200
    assert withdrawn.json()["withdrawn"] == 1
    assert _overrides(paths) == []
    reread = client.get(f"/api/agent-proposals/{run_id}").json()
    outcomes = {
        row["txn_id"]: row["outcome"]
        for row in reread["proposals"]
    }
    assert outcomes == {ids[0]: "withdrawn", ids[1]: "pending"}
    assert reread["state"] == "open", "withdrawing one answer does not close pending work"


def test_proposal_review_reads_current_transactions_and_lists_bounded_runs(
    client, paths, two_months
) -> None:
    ids = _eligible_proposal_ids(client)
    body = _proposal_body(client, ids)
    submitted = client.post("/api/agent-proposals", json=body).json()
    run_id = submitted["run_id"]

    listing = client.get("/api/agent-proposals", params={"limit": 1})
    assert listing.status_code == 200
    assert listing.json() == [{
        "run_id": run_id,
        "created_at": listing.json()[0]["created_at"],
        "state": "open",
        "producer": {**body["producer"], "model_reported": None},
        "proposal_count": 2,
        "pending": 2,
        "accepted": 0,
        "edited": 0,
        "rejected": 0,
        "withdrawn": 0,
    }]
    assert client.get("/api/agent-proposals", params={"limit": 101}).status_code == 422

    run = client.get(f"/api/agent-proposals/{run_id}").json()
    current = {row["txn_id"]: row["current_transaction"] for row in run["proposals"]}
    expected = {
        row["txn_id"]: row
        for row in client.get("/api/transactions", params={"limit": 500}).json()["items"]
        if row["txn_id"] in ids
    }
    assert current == expected

    # The review view re-reads this fact from the ledger. It does not preserve
    # a copy from the proposal submission and pretend that copy is current.
    changed = client.patch(
        f"/api/transactions/{ids[0]}", json={"category_id": "groceries"}
    )
    assert changed.status_code == 200
    reread = client.get(f"/api/agent-proposals/{run_id}").json()
    changed_row = next(row for row in reread["proposals"] if row["txn_id"] == ids[0])
    assert changed_row["current_transaction"]["category_id"] == "groceries"
    assert changed_row["current_transaction"]["category_decided_by"] == "override"


def test_proposal_writes_refuse_stale_revision_extra_filter_and_duplicate_selection(
    client, paths, two_months
) -> None:
    ids = _eligible_proposal_ids(client)
    body = _proposal_body(client, ids)

    stale = dict(body)
    stale["ledger_revision"] = "sha256:" + "0" * 64
    assert client.post("/api/agent-proposals", json=stale).status_code == 409

    extra = dict(body)
    extra["filter"] = {"direction": "out"}
    assert client.post("/api/agent-proposals", json=extra).status_code == 422

    duplicate_group = {
        "group_id": group_id_for("groceries", (ids[0],)),
        "category_id": "groceries",
        "txn_ids": [ids[0]],
    }
    duplicate = dict(body)
    duplicate["groups"] = [body["groups"][0], duplicate_group]
    assert client.post("/api/agent-proposals", json=duplicate).status_code == 409
    assert _overrides(paths) == []
    with reading(paths) as conn:
        assert conn.execute("SELECT COUNT(*) FROM agent_proposal_run").fetchone()[0] == 0


def test_proposal_review_write_schema_rejects_ambiguous_or_repeated_ids(
    client, paths, two_months
) -> None:
    ids = _eligible_proposal_ids(client)
    run_id = client.post(
        "/api/agent-proposals", json=_proposal_body(client, ids)
    ).json()["run_id"]

    reject_with_category = client.post(
        f"/api/agent-proposals/{run_id}/review",
        json={"action": "reject", "txn_ids": [ids[0]], "category_id": "dining"},
    )
    repeated = client.post(
        f"/api/agent-proposals/{run_id}/review",
        json={"action": "accept", "txn_ids": [ids[0], ids[0]]},
    )
    extra = client.post(
        f"/api/agent-proposals/{run_id}/review",
        json={"action": "accept", "txn_ids": [ids[0]], "filter": {"month": "2025-01"}},
    )

    assert (reject_with_category.status_code, repeated.status_code, extra.status_code) == (
        422, 422, 422
    )
    assert _overrides(paths) == []


# --- A6.5: exhaustive remaining-coverage triage stays a separate audit ----


def _triage_body(paths: DataPaths, client) -> tuple[dict, list[str]]:
    ids = _eligible_proposal_ids(client, count=3)
    all_rows = client.get("/api/transactions", params={"limit": 500}).json()["items"]
    eligible = [row["txn_id"] for row in all_rows if row["category_decided_by"] == "none"]
    assert len(eligible) >= 3
    definitions = (
        ("possible_transfer", "account_movement_language", tuple(eligible[:1])),
        ("taxonomy_gap", "coherent_activity_missing", tuple(eligible[1:2])),
        ("uncertain", "descriptor_ambiguous", tuple(eligible[2:])),
    )
    with reading(paths) as conn:
        draft = TriageDraft(
            schema_version=1,
            ledger_revision=ledger_revision(conn),
            scope=TriageScope(),
            producer=Producer(client="claude-code", client_version="api-test"),
            groups=tuple(
                TriageGroup(
                    group_id=triage_group_id_for(route, reason, txn_ids),
                    route=route,
                    reason_code=reason,
                    txn_ids=txn_ids,
                )
                for route, reason, txn_ids in definitions
            ),
        )
        normalized = validate_triage(conn, paths, draft).submission
    assert set(ids).issubset(eligible)
    return triage_to_wire(normalized), eligible


def test_triage_submit_review_and_withdraw_keep_human_decisions_explicit(
    client, paths, two_months
) -> None:
    body, ids = _triage_body(paths, client)
    before_overrides = _overrides(paths)
    before_counts = row_counts_of(paths)

    submitted = client.post("/api/agent-triage", json=body)

    assert submitted.status_code == 201
    run_id = submitted.json()["run_id"]
    assert submitted.json()["item_count"] == len(ids)
    assert _overrides(paths) == before_overrides
    assert row_counts_of(paths)["posting"] == before_counts["posting"]

    listing = client.get("/api/agent-triage", params={"limit": 1}).json()
    assert listing[0]["run_id"] == run_id
    assert (listing[0]["item_count"], listing[0]["pending"]) == (len(ids), len(ids))
    run = client.get(f"/api/agent-triage/{run_id}").json()
    summaries = {row["route"]: row for row in run["route_summaries"]}
    assert set(summaries) == {"possible_transfer", "taxonomy_gap", "uncertain"}
    assert sum(row["item_count"] for row in summaries.values()) == len(ids)
    assert all(isinstance(row["bank_amount_minor"], int) for row in summaries.values())

    possible = next(row for row in run["items"] if row["route"] == "possible_transfer")
    gap = next(row for row in run["items"] if row["route"] == "taxonomy_gap")
    uncertain = [row for row in run["items"] if row["route"] == "uncertain"]
    classified = client.post(
        f"/api/agent-triage/{run_id}/review",
        json={"action": "classify", "txn_ids": [possible["txn_id"]], "category_id": "transfer"},
    )
    confirmed_gap = client.post(
        f"/api/agent-triage/{run_id}/review",
        json={"action": "confirm_gap", "txn_ids": [gap["txn_id"]]},
    )
    left = client.post(
        f"/api/agent-triage/{run_id}/review",
        json={"action": "leave_uncertain", "txn_ids": [row["txn_id"] for row in uncertain]},
    )

    assert classified.json()["confirmed_transfer"] == 1
    assert confirmed_gap.json()["confirmed_taxonomy_gap"] == 1
    assert left.json()["left_uncertain"] == len(uncertain)
    assert left.json()["state"] == "completed"
    assert _overrides(paths) == [(possible["txn_id"], "transfer")]
    assert row_counts_of(paths)["posting"] == before_counts["posting"]

    invalid_selected = client.post(
        f"/api/agent-triage/{run_id}/withdraw-selected",
        json={"txn_ids": [gap["txn_id"]]},
    )
    assert invalid_selected.status_code == 409
    withdrawn = client.post(
        f"/api/agent-triage/{run_id}/withdraw-selected",
        json={"txn_ids": [possible["txn_id"]]},
    )
    assert withdrawn.json()["withdrawn"] == 1
    assert _overrides(paths) == []


def test_triage_http_rejects_stale_scope_unknown_fields_and_ambiguous_review(
    client, paths, two_months
) -> None:
    body, ids = _triage_body(paths, client)
    extra = {**body, "confidence": 0.9}
    assert client.post("/api/agent-triage", json=extra).status_code == 422

    client.patch(f"/api/transactions/{ids[0]}", json={"category_id": "dining"})
    stale = client.post("/api/agent-triage", json=body)
    assert stale.status_code == 409
    assert row_counts_of(paths)["agent_triage_run"] == 0

    client.patch(f"/api/transactions/{ids[0]}", json={"category_id": None})
    fresh, _ = _triage_body(paths, client)
    run_id = client.post("/api/agent-triage", json=fresh).json()["run_id"]
    repeated = client.post(
        f"/api/agent-triage/{run_id}/review",
        json={"action": "leave_uncertain", "txn_ids": [ids[0], ids[0]]},
    )
    wrong_category = client.post(
        f"/api/agent-triage/{run_id}/review",
        json={"action": "confirm_gap", "txn_ids": [ids[1]], "category_id": "dining"},
    )
    assert (repeated.status_code, wrong_category.status_code) == (422, 422)


# --- PATCH: what a person decides -------------------------------------------


def test_patching_a_transaction_this_ledger_does_not_have_writes_nothing(
    client, paths, two_months
) -> None:
    before, overrides = _rule_columns(paths), _overrides(paths)

    refused = client.patch(f"/api/transactions/{'0' * 64}", json={"category_id": "dining"})

    assert refused.status_code == 404
    assert _rule_columns(paths) == before
    assert _overrides(paths) == overrides == []


def test_patching_to_a_category_this_ledger_never_mirrored_writes_nothing(
    client, paths, two_months
) -> None:
    """422 rather than 404: retrying with a different body works.

    That is the distinction from the 422 in ``routes/statements.py``, where no
    body will ever make the answer yes.
    """
    txn_id = _only(client, GROCERIES_LINE)["txn_id"]
    before = _rule_columns(paths)

    refused = client.patch(f"/api/transactions/{txn_id}", json={"category_id": "not-a-category"})

    assert refused.status_code == 422
    assert "not-a-category" in refused.json()["detail"]
    assert _rule_columns(paths) == before
    assert _overrides(paths) == [], "an unknown category must not leave a row behind"

    # The negative case: the same transaction, a category the ledger has.
    accepted = client.patch(f"/api/transactions/{txn_id}", json={"category_id": "dining"})
    assert accepted.status_code == 200
    assert _overrides(paths) == [(txn_id, "dining")]


def test_a_body_without_the_field_is_refused_so_an_empty_one_cannot_clear_a_decision(
    client, paths, two_months
) -> None:
    """``null`` is an instruction; a missing field is a mistake, and they differ.

    A ledger where an empty PATCH silently withdrew somebody's correction would
    lose a decision ``archive/`` cannot rebuild (``docs/STATUS.md`` §5.49).
    """
    txn_id = _only(client, GROCERIES_LINE)["txn_id"]
    client.patch(f"/api/transactions/{txn_id}", json={"category_id": "dining"})
    assert _overrides(paths) == [(txn_id, "dining")]

    empty = client.patch(f"/api/transactions/{txn_id}", json={})

    assert empty.status_code == 422
    assert _overrides(paths) == [(txn_id, "dining")], "the decision is still there"

    # Spelled out, the same request is accepted and does withdraw it.
    explicit = client.patch(f"/api/transactions/{txn_id}", json={"category_id": None})
    assert explicit.status_code == 200
    assert _overrides(paths) == []


def test_setting_a_category_answers_with_the_effective_value(client, two_months) -> None:
    line = _only(client, GROCERIES_LINE)
    assert line["category_id"] == "groceries" and line["category_decided_by"] == "rule"

    response = client.patch(f"/api/transactions/{line['txn_id']}", json={"category_id": "dining"})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["changed"] is True
    assert body["transaction"]["category_id"] == "dining"
    assert body["transaction"]["category_decided_by"] == "override"
    assert body["summary"], "the row's status line has to say something"

    # And the list agrees, which is the half a response echoing its own request
    # would not prove.
    assert _only(client, GROCERIES_LINE)["category_id"] == "dining"


def test_setting_the_same_category_twice_reports_the_second_as_no_change(
    client, paths, two_months
) -> None:
    """``changed`` is the only evidence the caller has that the click landed."""
    txn_id = _only(client, GROCERIES_LINE)["txn_id"]

    first = client.patch(f"/api/transactions/{txn_id}", json={"category_id": "dining"})
    stored = _overrides(paths)
    second = client.patch(f"/api/transactions/{txn_id}", json={"category_id": "dining"})

    assert first.json()["changed"] is True
    assert second.status_code == 200
    assert second.json()["changed"] is False
    assert second.json()["transaction"] == first.json()["transaction"]
    assert _overrides(paths) == stored == [(txn_id, "dining")]


def test_withdrawing_a_decision_gives_the_line_back_to_the_rules(client, two_months) -> None:
    line = _only(client, GROCERIES_LINE)
    client.patch(f"/api/transactions/{line['txn_id']}", json={"category_id": "dining"})

    response = client.patch(f"/api/transactions/{line['txn_id']}", json={"category_id": None})

    assert response.status_code == 200
    assert response.json()["changed"] is True
    assert response.json()["transaction"]["category_id"] == "groceries"
    assert response.json()["transaction"]["category_decided_by"] == "rule"


def test_withdrawing_a_decision_on_a_line_no_rule_claimed_reports_none(client, two_months) -> None:
    """The third value, and the reason there are three.

    Reporting "no rule claimed this" as ``rule`` would be a field claiming a
    decision nobody made, on the largest single block of data this project has.
    """
    line = _only(client, UNCLAIMED_LINE)
    assert line["category_id"] is None and line["category_decided_by"] == "none"

    client.patch(f"/api/transactions/{line['txn_id']}", json={"category_id": "dining"})
    withdrawn = client.patch(f"/api/transactions/{line['txn_id']}", json={"category_id": None})

    assert withdrawn.json()["transaction"]["category_id"] is None
    assert withdrawn.json()["transaction"]["category_decided_by"] == "none"


def test_clearing_a_decision_nobody_made_is_accepted_and_reported_as_no_change(
    client, paths, two_months
) -> None:
    txn_id = _only(client, GROCERIES_LINE)["txn_id"]

    response = client.patch(f"/api/transactions/{txn_id}", json={"category_id": None})

    assert response.status_code == 200
    assert response.json()["changed"] is False
    assert _overrides(paths) == []


def test_naming_the_transfer_category_makes_the_line_a_transfer(client, two_months) -> None:
    """One table, one sentence — there is no ``is_transfer`` field to send."""
    line = _only(client, GROCERIES_LINE)
    assert line["is_transfer"] is False and line["transfer_decided_by"] == "rule"

    body = client.patch(
        f"/api/transactions/{line['txn_id']}", json={"category_id": "transfer"}
    ).json()

    assert body["transaction"]["is_transfer"] is True
    assert body["transaction"]["transfer_decided_by"] == "override"
    assert len(_items(client, transfer=True)) == RULE_TRANSFERS + 1


def test_naming_an_expense_category_takes_a_flagged_transfer_back_out(client, two_months) -> None:
    """The direction that matters most: a false positive shrinks spending silently."""
    line = _only(client, TRANSFER_LINE)
    assert line["is_transfer"] is True and line["transfer_decided_by"] == "rule"

    body = client.patch(
        f"/api/transactions/{line['txn_id']}", json={"category_id": "dining"}
    ).json()

    assert body["transaction"]["is_transfer"] is False
    assert body["transaction"]["transfer_decided_by"] == "override"
    assert body["transaction"]["category_id"] == "dining"
    assert _items(client, transfer=True) == [], "the rules' own flag no longer decides this line"


def test_the_patch_never_writes_either_of_the_rules_own_columns(client, paths, two_months) -> None:
    """``reapply-rules`` must be able to re-derive them without losing a person's.

    Asserted against the raw columns rather than against any response, because
    both views report the effective value and would hide exactly this.
    """
    before = _rule_columns(paths)
    groceries = _only(client, GROCERIES_LINE)["txn_id"]
    flagged = _only(client, TRANSFER_LINE)["txn_id"]

    moved = client.patch(f"/api/transactions/{groceries}", json={"category_id": "transfer"})
    unflagged = client.patch(f"/api/transactions/{flagged}", json={"category_id": "dining"})
    assert (moved.status_code, unflagged.status_code) == (200, 200)

    after = _rule_columns(paths)
    assert after == before, "posting.category_id and txn.is_transfer are the rules' answers"
    assert sorted(_overrides(paths)) == sorted(
        [(groceries, "transfer"), (flagged, "dining")]
    ), "and the person's answer went where a person's answer goes"

    # The negative case for the equality above: the same snapshot does move
    # when the rules' own writer runs. Without it, `after == before` would also
    # hold for a snapshot that could never change.
    conn = connect(paths.db)
    try:
        with transaction(conn):
            repo.set_transfer_flags(conn, assignments={groceries: True})
    finally:
        conn.close()
    assert _rule_columns(paths) != before


def test_a_transfer_marked_by_hand_moves_the_headline_and_leaves_verify_green(
    client, paths, two_months
) -> None:
    """The first time ``category_override`` is reachable in production.

    ``docs/STATUS.md`` §5.47 records the M2 window in which the two cashflow
    aggregations disagreed the moment somebody marked one line — the check that
    caught it is ``cashflow_agreement``, and this asserts it is still passing
    afterwards rather than only that the numbers moved.
    """
    before = client.get("/api/health").json()["totals"]
    assert before["transfer_count"] == RULE_TRANSFERS
    assert _not_passing(paths) == []

    line = _only(client, GROCERIES_LINE)
    marked = client.patch(f"/api/transactions/{line['txn_id']}", json={"category_id": "transfer"})
    assert marked.status_code == 200, marked.text

    after = client.get("/api/health").json()["totals"]
    assert after["transfer_count"] == before["transfer_count"] + 1
    assert after["transfer_excluded_out_minor"] == (
        before["transfer_excluded_out_minor"] + line["amount_minor"]
    ), "it says how much was taken out, not only that something was"
    assert after["outflow_minor"] == before["outflow_minor"] - line["amount_minor"]
    assert after["net_minor"] != before["net_minor"], "the headline really moved"

    results = _checks_of(paths)
    assert [c for c, r in results.items() if r.status != "pass"] == []
    assert results["cashflow_agreement"].status == "pass"
    assert len(results) == 9


def test_the_agreement_check_the_test_above_relies_on_can_still_fail_here(paths, client) -> None:
    """A check nobody has watched fail on this ledger has not been tested on it.

    The shape is ``docs/STATUS.md`` §5.45's first counterexample: a transaction
    with an expense leg and no ``txn_identity`` row. ``ledger_totals`` counts it
    because it never joins that table; ``v_cashflow_monthly`` cannot see it.
    Written straight into the database for the same reason the deletion test
    above writes rows by hand — the normal path cannot build it.
    """
    conn = connect(paths.db)
    try:
        with transaction(conn):
            repo.ensure_account(
                conn,
                account_id="assets:invisible:checking",
                name="Assets:Invisible:Checking",
                kind="asset",
                subtype="checking",
                currency="USD",
                institution="invisible",
                mask=None,
            )
            conn.execute(
                "INSERT INTO txn (id, date, narration, created_at) VALUES (?, ?, ?, ?)",
                ("ghost", "2025-01-09", "no identity row", INGESTED_AT),
            )
            for seq, (account, amount) in enumerate(
                (("assets:invisible:checking", -7_700), ("expenses:uncategorized", 7_700))
            ):
                conn.execute(
                    "INSERT INTO posting (id, txn_id, seq, account_id, amount_minor, currency) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (f"ghost-{seq}", "ghost", seq, account, amount, "USD"),
                )
    finally:
        conn.close()

    assert _checks_of(paths)["cashflow_agreement"].status == "fail"


# ---------------------------------------------------------------------------
# the shipped frontend
# ---------------------------------------------------------------------------

FORBIDDEN_JS = ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(")


def _web_files() -> list[Path]:
    return sorted(path for path in WEB_ROOT.rglob("*") if path.is_file())


def test_the_frontend_exists_and_is_modular() -> None:
    names = {path.relative_to(WEB_ROOT).as_posix() for path in _web_files()}
    assert {"index.html", "css/app.css", "js/main.js", "js/api.js"} <= names


def test_dashboard_has_one_sidebar_directory_and_no_body_agent_card() -> None:
    html = (WEB_ROOT / "index.html").read_text(encoding="utf-8")

    assert 'id="workspace-sidebar"' in html
    assert 'id="agent-center"' not in html
    for target in (
        "ledger",
        "analytics",
        "transactions",
        "agent-proposals",
        "agent-triage",
        "statement-history",
        "advice",
        "review-queue",
    ):
        assert f'href="#{target}"' in html
        assert f'id="{target}"' in html


def _scanned_for_dom_strings() -> list[Path]:
    """Exactly the files the check below reads. Named so it can be asserted."""
    return [path for path in _web_files() if path.suffix in {".js", ".html"}]


def _dom_offenders(paths: list[Path], needle: str, *, root: Path) -> list[str]:
    """``path:line`` for every non-comment line containing *needle*.

    Lifted out of the test unchanged so that a second test can watch it find
    something. A check nobody has seen fail has not been tested (``docs/
    STATUS.md`` §9 rule 7), and this one has never had a positive case: it has
    only ever been run over a frontend that was already clean.
    """
    return [
        f"{path.relative_to(root).as_posix()}:{number}"
        for path in paths
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if needle in line and not line.lstrip().startswith(("//", "*", "/*", "<!--"))
    ]


@pytest.mark.parametrize("needle", FORBIDDEN_JS)
def test_the_frontend_never_builds_dom_from_strings(needle: str) -> None:
    """Merchant names and Zelle memos are third-party text, and they reach the page.

    The predecessor's descriptions flowed straight into an ``innerHTML``
    interpolation. Grepping the shipped assets is a blunt instrument and that is
    the point: it cannot be satisfied by a sanitiser someone believes in.
    """
    offenders = _dom_offenders(_scanned_for_dom_strings(), needle, root=WEB_ROOT)
    assert offenders == [], f"{needle} in shipped frontend: {offenders}"


def test_the_dom_check_reads_every_script_that_ships() -> None:
    """The scan has to grow when the frontend does, without anyone remembering.

    A guard whose file list is stale passes by not looking. This asserts the
    list is derived — every ``.js`` and ``.html`` under ``web/``, no exceptions
    — rather than enumerated, so a module added to the page is covered the
    moment it exists.
    """
    scanned = {path.resolve() for path in _scanned_for_dom_strings()}
    shipped = {
        path.resolve()
        for suffix in ("*.js", "*.html")
        for path in WEB_ROOT.rglob(suffix)
        if path.is_file()
    }

    assert scanned == shipped
    assert len(shipped) >= 2, "index.html and at least one script, or the scan is measuring nothing"


@pytest.mark.parametrize("needle", FORBIDDEN_JS)
def test_the_dom_check_finds_what_it_is_looking_for(git_free_tmp: Path, needle: str) -> None:
    """The negative case the real frontend cannot provide, one per needle.

    Written outside the repository so that nothing here becomes a shipped file.
    Two lines: one that uses the construct and one that only mentions it in a
    comment. Both halves matter — a check that flagged the comment would be a
    check people learn to work around by rewording.
    """
    sample = git_free_tmp / "not-shipped.js"
    sample.write_text(f"// mentions {needle} in a comment\nconst bad = {needle};\n", "utf-8")

    assert _dom_offenders([sample], needle, root=git_free_tmp) == ["not-shipped.js:2"]


def test_the_frontend_requests_nothing_off_origin() -> None:
    """No CDN, no font service, no analytics. It has to work offline."""
    offenders = [
        f"{path.relative_to(WEB_ROOT).as_posix()}:{number}"
        for path in _web_files()
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if "http://" in line or "https://" in line
    ]
    assert offenders == [], f"off-origin reference(s): {offenders}"


def test_every_frontend_file_carries_the_licence_header() -> None:
    missing = [
        path.relative_to(WEB_ROOT).as_posix()
        for path in _web_files()
        if "SPDX-License-Identifier: AGPL-3.0-or-later"
        not in path.read_text(encoding="utf-8")[:400]
    ]
    assert missing == []


def test_no_frontend_file_grows_past_the_split_line() -> None:
    """EXECUTION_PLAN §1.3: 400 lines is the signal to split, not a preference.

    The predecessor was one 5,092-line HTML file, and that is how its bugs
    survived a year of being looked at.
    """
    oversized = {
        path.relative_to(WEB_ROOT).as_posix(): len(path.read_text(encoding="utf-8").splitlines())
        for path in _web_files()
        if len(path.read_text(encoding="utf-8").splitlines()) > 400
    }
    assert oversized == {}


# ---------------------------------------------------------------------------
# When the table's figures equal the page's four, and when they do not
#
# This is here because the sentence that answers it was published, refuted,
# rewritten and refuted again -- once on the page, once in the OpenAPI
# description, once in two docstrings. `docs/STATUS.md` §5.43 records the same
# thing happening to a different sentence and the conclusion drawn there: a
# claim that keeps being wrong has stopped being a wording problem, and belongs
# in an assertion.
#
# The rule, and every case below is one row of its truth table:
#
#     they are equal exactly while the filtered list holds the lines
#     `ledger_totals` counts -- the non-transfer statement lines.
#
# What it is NOT about is whether somebody typed a filter. Both refutations came
# from filters that select everything.
# ---------------------------------------------------------------------------


def _table_figures(client, **params) -> tuple[int, int, int, int]:
    """The table's three figures and how many rows they describe."""
    totals = _page(client, limit=1, **params)["totals"]
    return (
        int(totals["bank_in_minor"]),
        int(totals["bank_out_minor"]),
        int(totals["bank_net_minor"]),
        int(totals["matched"]),
    )


def _header_figures(client) -> tuple[int, int, int, int]:
    """The four at the top of the page, in the same order, for comparison.

    Returns tuples rather than a boolean so that a failure prints the numbers.
    A check whose whole purpose is to put two figures beside each other, and
    which reports only that they differ, is the shape ``docs/STATUS.md`` §5.45
    records ``cashflow_agreement`` shipping with.
    """
    totals = dict(client.get("/api/health").json()["totals"])
    return (
        int(totals["inflow_minor"]),
        int(totals["outflow_minor"]),
        int(totals["net_minor"]),
        int(totals["txn_count"]),
    )


def test_the_table_and_the_header_agree_exactly_when_the_list_holds_their_rows(
    client, two_months
) -> None:
    """Six cases, arranged so that both directions of the refutation are here.

    This fixture already has one line the *rules* flag as a transfer, which is
    worth saying out loud: it is the case the author's own 13 statements do not
    contain (§5.52 measured zero), so the unfiltered table here holds one more
    row than the four figures count and the two differ **with no filter at all**
    — the first refuted wording's condition, failing.

    ``transfer=false`` then makes them equal. That is a filter, and it is the
    second refuted wording's condition, also failing. A search matching every
    descriptor leaves them unequal, because it selects the same twelve rows as
    no filter did.

    Which rows, never whether a filter was typed.
    """
    header = _header_figures(client)

    unfiltered = _table_figures(client)
    assert unfiltered != header, "the rules flagged one line here, so the list holds one more"
    assert unfiltered[3] == header[3] + 1

    assert _table_figures(client, q=" ") == unfiltered, (
        "a search matching every descriptor selects the same rows as no filter"
    )

    assert _table_figures(client, transfer=False) == header, (
        "and a filter is what lines them up — the condition the second wording called separating"
    )

    narrowed = _table_figures(client, direction="in")
    assert narrowed != header, "a narrower list is a different set of rows"
    assert narrowed[3] < header[3]

    # Any line the rules did not already flag; which one is not the point.
    line = _items(client, transfer=False, limit=1)[0]
    assert (
        client.patch(
            f"/api/transactions/{line['txn_id']}", json={"category_id": "transfer"}
        ).status_code
        == 200
    )

    header_after = _header_figures(client)
    assert header_after[3] == header[3] - 1, "a person's mark takes one more line out of the four"
    assert _table_figures(client, transfer=False) == header_after, (
        "and the same filter still selects exactly what they now count"
    )


def row_counts_of(paths: DataPaths) -> dict[str, int]:
    with reading(paths) as conn:
        return row_counts(conn)


# ---------------------------------------------------------------------------
# P2 M5: both charts, over HTTP
#
# `tests/test_analytics.py` already pins what the two views measure, against
# mutations of the SQL. Nothing here re-measures that. What is not reachable
# from there is the part a person actually looks at:
#
#   * the two charts arrive in ONE response, so they cannot describe two
#     different states of the ledger;
#   * `categories.total_minor` is the same number as `/api/health`'s
#     `totals.outflow_minor` **as served**, not merely as queried. A breakdown
#     whose slices add up to something near the Out printed above them is the
#     shape §5.45 cost this project a block-level check to settle;
#   * a slice for the lines no rule claimed survives the trip to the wire with
#     its `null` intact. §5.38 is what a bucket named "other" hides.
#
# Same fixture as M4's tests, so the numbers below are the same twelve lines
# read a third way. Pinned as numbers rather than as `> 0` for §5.44's reason.
# ---------------------------------------------------------------------------

#: The one line the shipped transfer rules claim, on the bank leg. Negative.
TRANSFER_LINE_MINOR = -10_000

#: `/api/health`'s Out, and therefore what the breakdown has to add up to: the
#: bank-leg Out with the flagged transfer taken back out of it. Written as the
#: subtraction so that the relationship is visible rather than only the result.
SPEND_MINOR = BANK_OUT_MINOR - TRANSFER_LINE_MINOR

#: Transactions with an expense leg. Deliberately **not** `/api/health`'s
#: `txn_count`, which counts income and spending together — the two are not
#: comparable and `CategoryBreakdownOut` says so.
SPEND_TXNS = 9

#: The `category_id: null` slice: three withdrawals no rule claimed.
UNCLAIMED_SPEND_MINOR = -8_500
UNCLAIMED_SPEND_TXNS = 3

#: What `GROCERIES_LINE` costs, which is what marking it a transfer removes.
GROCERIES_SPEND_MINOR = -5_000

#: Oldest first, which is the direction a time axis reads.
CHART_MONTHS = ["2025-01", "2025-02"]

EMPTY_MONTHLY = {
    "months": [],
    "inflow_minor": 0,
    "outflow_minor": 0,
    "net_minor": 0,
    "txn_count": 0,
}
EMPTY_CATEGORIES = {"slices": [], "total_minor": 0, "txn_count": 0}

#: What the endpoint echoes back when no range was asked for. Asserted rather
#: than ignored: a client has to be able to tell what window it actually got,
#: and "both ends open" is an answer.
UNBOUNDED_SPAN = {"since": None, "until": None}


def _analytics(client) -> dict:
    response = client.get("/api/analytics")
    assert response.status_code == 200, response.text
    return dict(response.json())


def _slices(client) -> dict[str | None, int]:
    """``category_id -> spend_minor``, with ``None`` kept as a key of its own."""
    slices = _analytics(client)["categories"]["slices"]
    return {part["category_id"]: part["spend_minor"] for part in slices}


def _month_rows(paths: DataPaths) -> list[dict]:
    """The monthly decomposition, taken straight from SQL by a second route.

    Written out here rather than read through ``repo.monthly_cashflow``, because
    this is the assertion that says the endpoint reports the ledger rather than
    an arrangement of its own -- and going through the code the route goes
    through would be the route agreeing with itself.

    Note this is keyed by the **transaction** date, which is what P2 M6 made the
    bars mean. ``v_cashflow_monthly`` is a different question (the statement
    month, on the bank leg) and is no longer what this chart is drawn from; it
    stays as the independent path ``verify``'s ``cashflow_agreement`` compares
    against.
    """
    with reading(paths) as conn:
        return [
            dict(row)
            for row in conn.execute(
                "SELECT substr(l.date, 1, 7) AS month, "
                "  COALESCE(-SUM(CASE WHEN l.account_kind = 'income' "
                "                     THEN l.amount_minor ELSE 0 END), 0) AS inflow_minor, "
                "  COALESCE(-SUM(CASE WHEN l.account_kind = 'expense' "
                "                     THEN l.amount_minor ELSE 0 END), 0) AS outflow_minor, "
                "  COUNT(DISTINCT l.txn_id) AS txn_count "
                "FROM v_cashflow_line l WHERE l.is_transfer = 0 "
                "GROUP BY month ORDER BY month"
            )
        ]


# --- the empty and absent cases --------------------------------------------


def test_analytics_before_anything_is_ingested_is_empty_and_says_zero(client) -> None:
    """Zeroes are truthful here, unlike ``/api/health``'s totals.

    Each figure is a sum over a list this same response carries, so the reader
    can see the empty list it was taken over — a measurement. ``totals`` is
    ``null`` on an empty ledger because it carries a ``balance_minor``, and
    $0.00 there would read as a fact about money rather than as the absence of
    any.
    """
    body = _analytics(client)

    assert body == {
        "span": UNBOUNDED_SPAN,
        "totals": None,
        "monthly": EMPTY_MONTHLY,
        "categories": EMPTY_CATEGORIES,
    }
    assert client.get("/api/health").json()["totals"] is None, (
        "the same empty ledger, and the endpoint that must not say zero either"
    )


def test_analytics_without_a_database_answers_rather_than_failing(client, paths) -> None:
    """The other empty branch: no file at all, and still not a 500.

    Removed after ``create_app`` built it, because migration happens at startup
    and there is no other way to reach the branch — the same real state
    ``/api/transactions`` is tested against. A data directory is a directory,
    and nothing stops it being moved out from under a running server.
    """
    paths.db.unlink()

    body = _analytics(client)
    assert body == {
        "span": UNBOUNDED_SPAN,
        "totals": None,
        "monthly": EMPTY_MONTHLY,
        "categories": EMPTY_CATEGORIES,
    }


def test_analytics_with_a_ledger_is_not_the_empty_answer(client, two_months) -> None:
    """The negative case for the two above: the same call, a ledger behind it."""
    body = _analytics(client)

    assert body["monthly"]["months"] != []
    assert body["categories"]["slices"] != []
    assert body["monthly"]["txn_count"] > 0
    assert body["categories"]["total_minor"] < 0


def test_security_headers_are_on_the_analytics_response(client) -> None:
    """Named here rather than added to the table above, same helper either way."""
    response = client.get("/api/analytics")
    assert response.status_code == 200
    assert_security_headers(response)


# --- the monthly chart ------------------------------------------------------


def test_the_months_come_back_oldest_first_and_are_what_the_view_says(
    client, paths, two_months
) -> None:
    """Oldest first is the direction a time axis reads, and it is asserted twice.

    Once against the literal list, so a reversal is caught even if the view
    ever returns one row; and once field for field against
    ``v_cashflow_monthly`` itself, so the endpoint cannot quietly rebucket,
    rename or drop anything on the way out.
    """
    months = _analytics(client)["monthly"]["months"]

    assert [month["month"] for month in months] == CHART_MONTHS
    assert [month["month"] for month in months] == sorted(month["month"] for month in months)
    plain = _month_rows(paths)
    assert [
        {key: month[key] for key in ("month", "inflow_minor", "outflow_minor", "txn_count")}
        for month in months
    ] == plain, "the endpoint reports the ledger, not a second opinion"
    assert all(m["inflow_minor"] + m["outflow_minor"] == m["net_minor"] for m in months)


def test_the_monthly_sums_are_the_figures_at_the_top_of_the_page(client, two_months) -> None:
    """The bars' own total is a **checked** quantity, and this is the check's shape.

    ``verify``'s ``cashflow_agreement`` compares exactly these two aggregations
    — ``repo.ledger_totals`` against ``v_cashflow_monthly`` — on the operator's
    own ledger, and ``docs/STATUS.md`` §5.47 records the M2 window in which they
    disagreed the moment somebody marked one line. This asserts the same
    equality where a client sees it: over HTTP, between two endpoints.

    So a failure here is not "the chart is wrong". It is the two aggregations
    having come apart, which is a block-level check going red.
    """
    monthly = _analytics(client)["monthly"]
    headline = client.get("/api/health").json()["totals"]

    assert monthly["inflow_minor"] == headline["inflow_minor"] == BANK_IN_MINOR
    assert monthly["outflow_minor"] == headline["outflow_minor"] == SPEND_MINOR
    assert monthly["txn_count"] == headline["txn_count"] == ALL_ROWS - RULE_TRANSFERS

    # And the sums really are sums of the bars beside them, not a second query:
    # a figure under a chart that was measured separately is a figure that can
    # describe a different set of months than the bars above it.
    assert sum(month["inflow_minor"] for month in monthly["months"]) == monthly["inflow_minor"]
    assert sum(month["outflow_minor"] for month in monthly["months"]) == monthly["outflow_minor"]
    assert sum(month["txn_count"] for month in monthly["months"]) == monthly["txn_count"]
    assert monthly["inflow_minor"] + monthly["outflow_minor"] == monthly["net_minor"]


def test_the_flagged_transfer_is_absent_from_the_monthly_bars(client, two_months) -> None:
    """The negative case for the equality above: the bank leg is a different number.

    Without it, "the bars equal the headline" would also hold on a ledger where
    nothing was ever flagged, and would be measuring nothing.
    """
    monthly = _analytics(client)["monthly"]
    listed = _page(client, limit=50)["totals"]

    assert listed["bank_out_minor"] == BANK_OUT_MINOR
    assert monthly["outflow_minor"] == BANK_OUT_MINOR - TRANSFER_LINE_MINOR
    assert monthly["outflow_minor"] != listed["bank_out_minor"], (
        "one rule-flagged transfer is the whole of the difference, and it is not zero"
    )


# --- the category chart -----------------------------------------------------


def test_the_breakdown_total_is_the_out_printed_at_the_top_of_the_page(
    client, two_months
) -> None:
    """The equality the whole chart rests on, asserted between two responses.

    Every wedge claims to be part of ``/api/health``'s Out. If the slices add up
    to a number that is merely *near* it, the page has grown a fourth cashflow
    measurement, and ``docs/STATUS.md`` §5.45 records what the third one cost.
    ``migrations/0007`` reads the **expense** leg for exactly this reason, so
    this is the assertion that says the arrangement still holds after the trip
    through HTTP.
    """
    categories = _analytics(client)["categories"]
    headline = client.get("/api/health").json()["totals"]

    assert categories["total_minor"] == headline["outflow_minor"] == SPEND_MINOR
    assert categories["total_minor"] < 0, "spend is negative, in Out's own sign convention"

    # Not the same count, and not comparable: `txn_count` here counts the
    # transactions with an expense leg, while the headline counts income and
    # spending together. Asserted so that a future reader who lines them up gets
    # a failing test rather than a plausible-looking equality.
    assert categories["txn_count"] == SPEND_TXNS
    assert categories["txn_count"] != headline["txn_count"]


def test_the_slices_add_up_to_the_total_beside_them(client, two_months) -> None:
    """One fetch, one sum — so no client can draw wedges its own total contradicts."""
    categories = _analytics(client)["categories"]

    assert sum(part["spend_minor"] for part in categories["slices"]) == categories["total_minor"]
    assert sum(part["txn_count"] for part in categories["slices"]) == categories["txn_count"]
    assert len(categories["slices"]) > 1, "a single slice would make the sum above trivial"


def test_the_lines_no_rule_claimed_are_a_slice_and_keep_their_null(client, two_months) -> None:
    """``null`` reaches the wire as ``null``: not dropped, not renamed, not zero.

    ``docs/STATUS.md`` §5.38. A bucket that collects the leftovers is
    indistinguishable *in a chart* from one that was matched on purpose, which
    is how the predecessor's breakdown rendered perfectly while it was wrong.
    There is no ``uncategorized`` category in this ledger to fall into.

    The last assertion is the one that makes "not dropped" mean something: this
    slice carries real area, so removing it would break the equality the test
    above asserts rather than tidying the picture.
    """
    slices = _slices(client)

    assert None in slices, "the unclaimed lines are a slice, not a gap"
    assert slices[None] == UNCLAIMED_SPEND_MINOR
    named = {part for part in slices if part is not None}
    assert not named & {"other", "uncategorized", "", "none"}, (
        "there is no catch-all category in this ledger and this endpoint must not invent one"
    )

    unclaimed = [
        part
        for part in _analytics(client)["categories"]["slices"]
        if part["category_id"] is None
    ]
    assert len(unclaimed) == 1 and unclaimed[0]["txn_count"] == UNCLAIMED_SPEND_TXNS
    assert sum(slices.values()) - slices[None] != SPEND_MINOR, (
        "dropping it would leave the wedges no longer adding up to the headline Out"
    )


def test_the_slices_are_largest_first_and_the_same_order_twice(client, two_months) -> None:
    """Largest spend first, and a legend that does not reshuffle between loads.

    ``spend_minor`` is negative, so largest-first is ascending. The stability
    half is asserted because ``docs/STATUS.md`` §5.71 is the record of the
    lesson: SQL does not order tied rows, and a chart whose legend a reader
    cannot read twice is one they cannot check against anything. The ordering
    ends in ``category_id`` for that reason; here it is asserted at the wire.
    """
    first = _analytics(client)["categories"]["slices"]
    second = _analytics(client)["categories"]["slices"]

    spends = [part["spend_minor"] for part in first]
    assert spends == sorted(spends), "negative amounts: ascending is largest spend first"
    assert spends != sorted(spends, reverse=True), "the values differ, so the direction is measured"
    assert first == second, "two identical requests, one order"


# --- what a person's mark does to both charts -------------------------------


def test_marking_a_transfer_by_hand_leaves_both_charts_and_withdrawing_restores_them(
    client, two_months
) -> None:
    """One click, both charts, and the equality that has to survive it.

    "Transfers do not appear in the spending pie" is an acceptance item that was
    untestable until M4: the shipped rules claim none of the author's 415 real
    lines (``docs/STATUS.md`` §5.52), so before there was a way to mark one by
    hand the condition could not be reached at all.

    Both directions are here, and the restoring one matters most: a false
    positive that cannot be withdrawn is spending that has silently shrunk,
    which is the predecessor's headline failure. Restoration is asserted as
    equality with the whole response measured before the mark, not as "the
    numbers moved back".
    """
    before = _analytics(client)
    january_before = before["monthly"]["months"][0]
    assert _slices(client)["groceries"] == GROCERIES_SPEND_MINOR

    line = _only(client, GROCERIES_LINE)
    marked = client.patch(f"/api/transactions/{line['txn_id']}", json={"category_id": "transfer"})
    assert marked.status_code == 200, marked.text

    after = _analytics(client)
    headline_after = client.get("/api/health").json()["totals"]

    # Gone from the pie entirely — it is the only groceries line on this ledger.
    assert "groceries" not in _slices(client)
    assert after["categories"]["total_minor"] == SPEND_MINOR - GROCERIES_SPEND_MINOR
    assert after["categories"]["txn_count"] == SPEND_TXNS - 1

    # And gone from its month's bar, which is the same subtraction one chart over.
    january_after = after["monthly"]["months"][0]
    assert january_after["month"] == january_before["month"] == "2025-01"
    assert january_after["outflow_minor"] == january_before["outflow_minor"] - GROCERIES_SPEND_MINOR
    assert january_after["txn_count"] == january_before["txn_count"] - 1

    # The equality the whole chart rests on still holds afterwards. This is the
    # assertion that would have caught the M2 window §5.47 records, where the
    # two aggregations came apart the moment somebody marked one line.
    assert after["categories"]["total_minor"] == headline_after["outflow_minor"]
    assert after["monthly"]["outflow_minor"] == headline_after["outflow_minor"]

    withdrawn = client.patch(f"/api/transactions/{line['txn_id']}", json={"category_id": None})
    assert withdrawn.status_code == 200
    assert withdrawn.json()["transaction"]["category_id"] == "groceries"

    assert _analytics(client) == before, "withdrawing puts back exactly what marking took out"


# --- the generated document -------------------------------------------------


def test_the_analytics_endpoint_is_in_the_openapi_document(client) -> None:
    """The document the frontend and these tests read is the one that must have it.

    ``/docs`` renders blank under this application's CSP by design, so
    ``/openapi.json`` is not a nicety here — it is the whole published contract.
    """
    document = client.get("/openapi.json").json()

    assert "/api/analytics" in document["paths"], sorted(document["paths"])
    assert "get" in document["paths"]["/api/analytics"]

    schemas = document["components"]["schemas"]
    assert {"AnalyticsOut", "MonthlyCashflowOut", "CashflowMonthOut"} <= set(schemas)
    assert {"CategoryBreakdownOut", "CategorySliceOut"} <= set(schemas)

    # No prose field, and that is deliberate (§5.69): every other body in this
    # API carries a `summary` sentence, and the one on the transaction table was
    # refuted by two consecutive acceptance rounds. Numbers and ids only.
    assert "summary" not in schemas["AnalyticsOut"]["properties"]
    assert "summary" not in schemas["CategoryBreakdownOut"]["properties"]
    assert "summary" not in schemas["MonthlyCashflowOut"]["properties"]

    # Money says its units in its own name, everywhere, or the page cannot format
    # by suffix — `schemas`' own module docstring calls that load-bearing.
    for name in ("CashflowMonthOut", "CategorySliceOut"):
        money = [field for field in schemas[name]["properties"] if "minor" in field]
        assert money, name
        assert all(field.endswith("_minor") for field in money), name


# --- the date range, over HTTP (P2 M6) -------------------------------------
#
# The range is on transaction dates and it narrows the figures, both charts and
# the table together. What these assert is not that filtering works but that the
# page stays internally checkable while it is filtered: the wedges still sum to
# the Out, the months still sum to the four figures, and the window the server
# used is echoed back so a client can tell what it got.


def _analytics_span(client, **params) -> dict:
    response = client.get("/api/analytics", params=params)
    assert response.status_code == 200, response.text
    return dict(response.json())


def test_the_window_is_echoed_back_so_a_client_can_tell_what_it_got(
    client, two_months
) -> None:
    body = _analytics_span(client, since="2025-01-01", until="2025-01-31")
    assert body["span"] == {"since": "2025-01-01", "until": "2025-01-31"}

    assert _analytics_span(client)["span"] == UNBOUNDED_SPAN
    assert _analytics_span(client, since="2025-01-01")["span"] == {
        "since": "2025-01-01",
        "until": None,
    }


def test_a_window_narrows_the_figures_and_both_charts_together(client, two_months) -> None:
    """One window, three answers, and they still agree with each other.

    The equalities the whole panel rests on are asked again *inside* a filter,
    which is the case that did not exist before M6: a range that broke them
    would leave the page adding up only when nothing was selected.
    """
    whole = _analytics_span(client)
    january = _analytics_span(client, since="2025-01-01", until="2025-01-31")

    assert january["totals"]["txn_count"] < whole["totals"]["txn_count"]
    assert [month["month"] for month in january["monthly"]["months"]] == ["2025-01"]

    # The wedges still add up to the Out -- this window's Out.
    assert january["categories"]["total_minor"] == january["totals"]["outflow_minor"]
    assert january["categories"]["total_minor"] == sum(
        part["spend_minor"] for part in january["categories"]["slices"]
    )

    # And the months still add up to the figures above them.
    assert sum(m["inflow_minor"] for m in january["monthly"]["months"]) == (
        january["totals"]["inflow_minor"]
    )
    assert sum(m["outflow_minor"] for m in january["monthly"]["months"]) == (
        january["totals"]["outflow_minor"]
    )


def test_the_unscoped_figures_are_still_healths_figures(client, two_months) -> None:
    """No range means the whole ledger, and that has to be the same ledger.

    ``/api/health`` reports the unscoped totals and never takes a window. This
    pins that the analytics endpoint agrees with it when nothing is selected --
    so a client can tell "narrowed" from "different".
    """
    body = _analytics_span(client)
    headline = client.get("/api/health").json()["totals"]

    for field in ("inflow_minor", "outflow_minor", "net_minor", "txn_count", "balance_minor"):
        assert body["totals"][field] == headline[field], field


def test_a_window_the_ledger_has_nothing_in_is_empty_and_says_so(client, two_months) -> None:
    """Empty, not absent, and not an error: the question was answerable."""
    body = _analytics_span(client, since="2030-01-01", until="2030-12-31")

    assert body["monthly"]["months"] == []
    assert body["categories"]["slices"] == []
    assert body["categories"]["total_minor"] == 0
    assert body["totals"]["inflow_minor"] == 0
    assert body["totals"]["outflow_minor"] == 0


def test_the_balance_in_a_window_is_the_balance_at_its_end(client, two_months) -> None:
    """The one figure a range must not treat as a flow.

    Bounding a balance at both ends would report the movement within the window
    under a label that says Balance. Asserted as an equality against the same
    window with its opening bound removed, so it pins the rule rather than a
    number.
    """
    closed = _analytics_span(client, since="2025-02-01", until="2025-02-28")
    open_start = _analytics_span(client, until="2025-02-28")

    assert closed["totals"]["balance_minor"] == open_start["totals"]["balance_minor"]
    # ...while the flows really did narrow, or the assertion above proves nothing.
    assert closed["totals"]["txn_count"] < open_start["totals"]["txn_count"]


@pytest.mark.parametrize("path", ["/api/analytics", "/api/transactions"])
def test_a_reversed_range_is_refused_rather_than_answered_with_nothing(client, path) -> None:
    """"No rows matched" is true of it and useless, so it is not the answer."""
    response = client.get(path, params={"since": "2025-07-01", "until": "2025-06-30"})
    assert response.status_code == 422
    assert "after" in response.json()["detail"]


@pytest.mark.parametrize("path", ["/api/analytics", "/api/transactions"])
@pytest.mark.parametrize("value", ["2025-13-01", "2025-02-30"])
def test_a_date_that_is_not_a_day_is_refused(client, path, value) -> None:
    """Shape is not enough: a month of 13 matches the pattern and is not a date.

    Left through, it would compare as a string against dates that are all
    smaller and select nothing -- a filter answering "no rows" to a question
    nobody asked.
    """
    response = client.get(path, params={"since": value})
    assert response.status_code == 422


@pytest.mark.parametrize("path", ["/api/analytics", "/api/transactions"])
def test_a_malformed_date_is_refused_by_the_pattern(client, path) -> None:
    assert client.get(path, params={"since": "2025-W23-1"}).status_code == 422
    assert client.get(path, params={"until": "yesterday"}).status_code == 422


def test_the_range_narrows_the_table_by_the_same_column_the_charts_use(
    client, two_months
) -> None:
    """The table and the charts show one window, or the page cannot be read.

    The table's own ``month`` control stays and asks a different question --
    which statement a line is printed on. Both are sent together here to pin
    that they combine.
    """
    january = _page(client, since="2025-01-01", until="2025-01-31")
    assert january["totals"]["matched"] > 0
    assert {item["date"][:7] for item in january["items"]} == {"2025-01"}

    assert _page(client, since="2030-01-01")["totals"]["matched"] == 0

    both = _page(client, since="2025-01-01", until="2025-01-31", month="2025-02")
    assert both["totals"]["matched"] == 0, "a January date on a February statement: neither alone"
