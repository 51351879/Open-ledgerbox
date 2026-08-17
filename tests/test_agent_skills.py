# SPDX-License-Identifier: AGPL-3.0-or-later
"""Distribution guards for the shared-contract, modular Agent Skills."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "docs" / "AGENT_CONTRACT.md"
CODEX_SKILL = ROOT / ".agents" / "skills" / "ledgerbox" / "SKILL.md"
CLAUDE_SKILL = ROOT / ".claude" / "skills" / "ledgerbox" / "SKILL.md"
OPENAI_YAML = CODEX_SKILL.parent / "agents" / "openai.yaml"
KNOWLEDGE_ROOT = CODEX_SKILL.parent / "references"
KNOWLEDGE_FILES = {
    "workflow.md",
    "category-semantics.md",
    "transfer-boundaries.md",
    "grouping-and-abstention.md",
    "ambiguous-cases.md",
    "privacy-and-output.md",
}
TRIAGE_CONTRACT = ROOT / "docs" / "TRIAGE_AGENT_CONTRACT.md"
CODEX_TRIAGE_SKILL = ROOT / ".agents" / "skills" / "ledgerbox-triage" / "SKILL.md"
CLAUDE_TRIAGE_SKILL = ROOT / ".claude" / "skills" / "ledgerbox-triage" / "SKILL.md"
TRIAGE_OPENAI_YAML = CODEX_TRIAGE_SKILL.parent / "agents" / "openai.yaml"
SETUP = ROOT / "docs" / "AGENT_SETUP.md"

EXPECTED_TOOLS = {
    "ledgerbox_status",
    "ledgerbox_categories",
    "ledgerbox_candidates",
    "ledgerbox_validate_proposal",
    "ledgerbox_submit_proposal",
}
EXPECTED_TRIAGE_TOOLS = {
    "ledgerbox_status",
    "ledgerbox_categories",
    "ledgerbox_candidates",
    "ledgerbox_validate_triage",
    "ledgerbox_submit_triage",
}


def test_wheel_maps_the_canonical_skills_into_one_runtime_workspace() -> None:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    force_include = config["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]
    expected = {
        ".agents/skills/ledgerbox/SKILL.md": (
            "ledgerbox/_agent_workspace/.agents/skills/ledgerbox/SKILL.md"
        ),
        ".agents/skills/ledgerbox/agents": (
            "ledgerbox/_agent_workspace/.agents/skills/ledgerbox/agents"
        ),
        ".agents/skills/ledgerbox/references": (
            "ledgerbox/_agent_workspace/.agents/skills/ledgerbox/references"
        ),
        ".agents/skills/ledgerbox-triage": (
            "ledgerbox/_agent_workspace/.agents/skills/ledgerbox-triage"
        ),
        ".claude/skills/ledgerbox": (
            "ledgerbox/_agent_workspace/.claude/skills/ledgerbox"
        ),
        ".claude/skills/ledgerbox-triage": (
            "ledgerbox/_agent_workspace/.claude/skills/ledgerbox-triage"
        ),
        "docs/AGENT_CONTRACT.md": "ledgerbox/_agent_workspace/docs/AGENT_CONTRACT.md",
        "docs/TRIAGE_AGENT_CONTRACT.md": (
            "ledgerbox/_agent_workspace/docs/TRIAGE_AGENT_CONTRACT.md"
        ),
        "docs/AGENT_SETUP.md": "ledgerbox/_agent_workspace/docs/AGENT_SETUP.md",
    }

    assert force_include == expected
    assert all((ROOT / source).exists() for source in force_include)


def test_skills_are_thin_and_share_one_contract_and_knowledge_source() -> None:
    contract = CONTRACT.read_text(encoding="utf-8")
    codex = CODEX_SKILL.read_text(encoding="utf-8")
    claude = CLAUDE_SKILL.read_text(encoding="utf-8")

    assert "docs/AGENT_CONTRACT.md" in codex
    assert "docs/AGENT_CONTRACT.md" in claude
    assert len(codex.splitlines()) <= 40
    assert len(claude.splitlines()) <= 40
    assert "Zelle" not in codex + claude
    assert "raw_descriptor" not in codex + claude
    assert "category_id" not in codex + claude
    for filename in KNOWLEDGE_FILES:
        assert filename in codex
        assert filename in claude
        assert (KNOWLEDGE_ROOT / filename).is_file()
    assert set(re.findall(r"`(ledgerbox_[a-z_]+)`", contract)) == EXPECTED_TOOLS


def test_classification_knowledge_is_modular_and_not_a_taxonomy_copy() -> None:
    knowledge = {
        path.name: path.read_text(encoding="utf-8")
        for path in KNOWLEDGE_ROOT.glob("*.md")
    }

    assert set(knowledge) == KNOWLEDGE_FILES
    combined = "\n".join(knowledge.values())
    assert "official-classification-v1" in combined
    assert "ledgerbox_categories" in combined
    assert "rules/categories.json" not in combined
    assert "24 categories" not in combined
    assert "24 类" not in combined


def test_classification_knowledge_covers_known_failure_boundaries() -> None:
    workflow = (KNOWLEDGE_ROOT / "workflow.md").read_text(encoding="utf-8")
    transfers = (KNOWLEDGE_ROOT / "transfer-boundaries.md").read_text(encoding="utf-8")
    grouping = (KNOWLEDGE_ROOT / "grouping-and-abstention.md").read_text(encoding="utf-8")
    examples = (KNOWLEDGE_ROOT / "ambiguous-cases.md").read_text(encoding="utf-8")
    privacy = (KNOWLEDGE_ROOT / "privacy-and-output.md").read_text(encoding="utf-8")

    assert "untrusted bank data" in workflow
    assert "payment rail" in transfers
    assert "ownership" in transfers
    assert "principal" in transfers
    assert "omit" in grouping.lower()
    assert "one category" in grouping
    assert "prompt injection" in examples
    assert "aggregate-only" in privacy
    assert "complete, truncated, or abbreviated" in privacy
    assert "proposal_schema_version" in workflow
    assert "application_mode" in workflow
    assert "review_first" in workflow
    assert "schema-version 1" in workflow
    assert "stop" in workflow.lower()


def test_agent_final_summary_has_a_narrow_non_identifying_shape() -> None:
    contract = CONTRACT.read_text(encoding="utf-8")
    codex = CODEX_SKILL.read_text(encoding="utf-8")
    claude = CLAUDE_SKILL.read_text(encoding="utf-8")

    assert "category-by-category breakdown" in contract
    assert "complete, truncated, or abbreviated" in contract
    assert "No effective category changed" in contract
    assert "application_mode" in contract
    assert "proposal_schema_version" in contract
    assert "review_first" in contract
    assert "automatic" in contract
    for skill in (codex, claude):
        assert "fixed aggregate-only final shape" in skill
        assert "category breakdown" in skill
        assert "truncated, or abbreviated run/revision ID" in skill


def test_skills_do_not_ship_a_private_connection_or_credentials() -> None:
    skill_files = list(CODEX_SKILL.parent.rglob("*")) + list(CLAUDE_SKILL.parent.rglob("*"))
    file_text = "\n".join(
        path.read_text(encoding="utf-8") for path in skill_files if path.is_file()
    )

    assert not (ROOT / ".mcp.json").exists()
    assert not (ROOT / ".codex" / "config.toml").exists()
    assert not (CLAUDE_SKILL.parent / "agents").exists()
    for forbidden in ("API_KEY", "Bearer ", "D:\\ledgerbox-data", "D:/ledgerbox-data"):
        assert forbidden not in file_text


def test_codex_skill_metadata_mentions_the_skill() -> None:
    metadata = OPENAI_YAML.read_text(encoding="utf-8")

    assert 'display_name: "Ledgerbox proposals"' in metadata
    assert 'short_description: "Prepare local classification proposals for review"' in metadata
    assert "$ledgerbox" in metadata


def test_triage_skills_are_thin_separate_and_share_one_contract() -> None:
    contract = TRIAGE_CONTRACT.read_text(encoding="utf-8")
    codex = CODEX_TRIAGE_SKILL.read_text(encoding="utf-8")
    claude = CLAUDE_TRIAGE_SKILL.read_text(encoding="utf-8")

    assert "docs/TRIAGE_AGENT_CONTRACT.md" in codex
    assert "docs/TRIAGE_AGENT_CONTRACT.md" in claude
    assert len(codex.splitlines()) <= 30
    assert len(claude.splitlines()) <= 30
    assert set(re.findall(r"`(ledgerbox_[a-z_]+)`", contract)) == EXPECTED_TRIAGE_TOOLS
    for skill in (codex, claude):
        assert "every currently unanswered" in skill
        assert "has_more: false" in skill
        assert "Do not call either category-proposal tool" in skill
        assert "aggregate-only final shape" in skill


def test_triage_contract_and_metadata_keep_the_no_write_boundary_visible() -> None:
    contract = TRIAGE_CONTRACT.read_text(encoding="utf-8")
    metadata = TRIAGE_OPENAI_YAML.read_text(encoding="utf-8")

    assert "Submission writes only `agent_triage_run` and `agent_triage_item`" in contract
    assert "Effective categories changed: no" in contract
    assert "Do not include descriptors, amounts, transaction ids" in contract
    assert 'display_name: "Ledgerbox coverage triage"' in metadata
    assert "$ledgerbox-triage" in metadata
    assert not (CLAUDE_TRIAGE_SKILL.parent / "agents").exists()


def test_claude_windows_setup_carries_no_child_flags_and_no_inner_quotes() -> None:
    """Both observed real failures stay named: `--` still loses child flags to
    Claude's own parser, and a JSON argument is mangled at the PowerShell/npm-shim
    boundary. The environment form has neither shape."""
    setup = SETUP.read_text(encoding="utf-8")

    assert "claude mcp add --scope local ledgerbox $bridge" in setup
    assert "-e LEDGERBOX_MCP_CLIENT=claude-code" in setup
    assert "-e 'LEDGERBOX_DATA_DIR=D:\\Ledgerbox Data\\mine'" in setup
    assert setup.index("$bridge -e") > setup.index("ledgerbox $bridge"), (
        "the variadic -e must come after the command positional or it swallows it"
    )
    assert "claude mcp add-json" not in setup.replace("`mcp add-json` loses", "")
    assert "claude mcp get ledgerbox" in setup
    assert "claude mcp add --transport stdio" not in setup
