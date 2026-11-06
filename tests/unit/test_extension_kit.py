"""What `relay new` generates must work untouched, and the PII inventory must not drift."""

from __future__ import annotations

import subprocess
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from relay.cli import app
from relay.config import REPO_ROOT, get_config
from relay.core.dsl.model import parse_definition
from relay.core.policy.packs import load_dir
from relay.core.policy.service import DEFAULT_POLICY_DIR
from relay.core.registry.validate import validate_definition
from relay.pii_inventory import generate, render
from relay.scaffold import ScaffoldError, new_connector, new_skill, new_workflow
from relay.skills.base import SkillManifest, load_manifest

NAMES = ["intake", "document_extractor", "exception_analyst", "comms_drafter", "helpdesk"]


def _pytest(*paths: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *map(str, paths)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture
def real_files() -> Iterator[list[Path]]:
    """Generated into the real tree, as a developer would, and always removed afterwards."""
    created: list[Path] = []
    yield created
    for path in created:
        path.unlink(missing_ok=True)
        if path.parent.name.startswith("zz") and path.parent.exists():
            path.parent.rmdir()


def _unique() -> str:
    return f"zz{uuid.uuid4().hex[:8]}"


@pytest.mark.db
def test_a_new_workflow_validates_and_its_own_test_passes(real_files: list[Path]) -> None:
    name = _unique()
    real_files += new_workflow(name)
    workflow = next(p for p in real_files if p.suffix == ".yaml" and "workflows" in p.parts)
    policies, _ = load_dir(DEFAULT_POLICY_DIR)
    definition = parse_definition(workflow.read_text(encoding="utf-8"))
    assert validate_definition(definition, policies) == []
    result = _pytest(next(p for p in real_files if p.suffix == ".py"))
    assert result.returncode == 0, result.stdout[-2000:]


def test_a_new_skill_works_with_ai_off(real_files: list[Path]) -> None:
    name = _unique()
    real_files += new_skill(name)
    assert load_manifest(name).skill == name
    result = _pytest(next(p for p in real_files if p.name.startswith("test_")))
    assert result.returncode == 0, result.stdout[-2000:]


def test_a_new_connector_loads_and_its_contract_test_passes(real_files: list[Path]) -> None:
    real_files += new_connector(_unique())
    result = _pytest(next(p for p in real_files if p.suffix == ".py"))
    assert result.returncode == 0, result.stdout[-2000:]


@pytest.mark.parametrize("make", [new_workflow, new_skill, new_connector])
@pytest.mark.parametrize("extra", [0, 14])  # 10 + 14 = the longest name allowed
def test_generated_code_passes_the_repos_own_lint(
    real_files: list[Path], make: Any, extra: int
) -> None:
    """CI runs ruff on everything, so a developer's first PR must not fail on our template."""
    real_files += make(_unique() + "x" * extra)
    code = [str(path) for path in real_files if path.suffix == ".py"]
    for command in (["check"], ["format", "--check"]):
        result = subprocess.run(
            [sys.executable, "-m", "ruff", *command, *code],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stdout


@pytest.mark.parametrize("make", [new_workflow, new_skill, new_connector])
def test_nothing_is_overwritten(tmp_path: Path, make: object) -> None:
    make("my_thing", tmp_path)  # type: ignore[operator]
    written = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    with pytest.raises(ScaffoldError, match="Already exists"):
        make("my_thing", tmp_path)  # type: ignore[operator]
    assert {p: p.read_bytes() for p in written} == written


@pytest.mark.parametrize(
    "bad", ["", "A", "has space", "../evil", "1abc", "semi;colon", "UPPER", "a" * 25]
)
def test_bad_names_are_refused(tmp_path: Path, bad: str) -> None:
    with pytest.raises(ScaffoldError):
        new_workflow(bad, tmp_path)
    assert not list(tmp_path.iterdir())


def test_cli_lists_what_it_created_and_reports_errors() -> None:
    runner = CliRunner()
    bad = runner.invoke(app, ["new", "workflow", "../evil"])
    assert bad.exit_code == 1
    assert "lower_snake_case" in bad.output


def test_inventory_covers_every_skill_and_says_where_models_run() -> None:
    text = generate()
    for name in NAMES:
        assert f"### {name} " in text
    assert "mock/deterministic" in text
    assert "Known gap" in text


def test_the_committed_inventory_is_current() -> None:
    committed = (REPO_ROOT / "docs" / "pii-inventory.md").read_text(encoding="utf-8")
    assert committed == generate(), "Run: uv run relay docs pii-inventory"


def test_inventory_follows_a_provider_change() -> None:
    config = get_config().models.model_copy(deep=True)
    updated = config.model_validate(
        {
            **config.model_dump(),
            "providers": {
                "real": {
                    "litellm_model": "acme/small",
                    "price_inr": {"in_per_1k": 1, "out_per_1k": 1},
                    "region": "ap-south-1",
                }
            },
            "tiers": {"tier1": "real", "tier2": "real"},
        }
    )
    text = render(updated, [load_manifest("helpdesk")])
    assert "`acme/small` | ap-south-1" in text


@pytest.mark.parametrize("name", NAMES)
def test_every_manifest_declares_its_data(name: str) -> None:
    manifest: SkillManifest = load_manifest(name)
    assert manifest.data_seen
    assert manifest.masking
    assert manifest.model_use
    assert manifest.retention


def test_the_guide_only_names_commands_that_exist() -> None:
    guide = (REPO_ROOT / "docs" / "developer-guide.md").read_text(encoding="utf-8")
    runner = CliRunner()
    for command in (
        ["new", "workflow", "--help"],
        ["new", "skill", "--help"],
        ["new", "connector", "--help"],
        ["validate", "--help"],
        ["policy", "test", "--help"],
        ["docs", "pii-inventory", "--help"],
        ["audit", "verify", "--help"],
    ):
        assert runner.invoke(app, command).exit_code == 0, command
        assert " ".join(command[:-1]) in guide, command
