#!/usr/bin/env python3
from __future__ import annotations
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

NAME_RE: Any = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
ALLOWED_FRONTMATTER_KEYS: Any = {"name", "description"}
REQUIRED_DIRS: Any = ["references", "assets", "scripts", "evals", "agents", "examples"]
REQUIRED_FILES: Any = [
    "references/01_data_first_market_router.md",
    "references/02_serenity_bottleneck_workflow.md",
    "references/03_fundamental_valuation_framework.md",
    "references/04_chan_technical_framework.md",
    "references/05_output_templates.md",
    "references/06_risk_compliance_no_guess.md",
    "references/15_ai_overlay_execution_protocol.md",
    "references/16_laplace_strategy_bridge.md",
    "references/17_industry_domain_packs.md",
    "assets/analysis_request.schema.json",
    "assets/evidence_ledger.schema.json",
    "assets/falsification_dashboard.schema.json",
    "assets/data_acquisition_policy.json",
    "assets/fetch_attempt_ledger.schema.json",
    "assets/data_gaps.schema.json",
    "assets/manual_retrieval_tasks.schema.json",
    "assets/valuation_inputs.schema.json",
    "assets/ai_research_dossier.schema.json",
    "assets/ai_research_overlay.schema.json",
    "assets/ai_review_outcome.schema.json",
    "assets/agent_research_queue.schema.json",
    "assets/research_workflow_state.schema.json",
    "assets/capital_action_quantification.schema.json",
    "assets/data_consumption_audit.schema.json",
    "assets/research_debt_runbook.schema.json",
    "assets/report_mode.schema.json",
    "assets/capital_actions.schema.json",
    "assets/technical_health.schema.json",
    "assets/comparison_output_contract.schema.json",
    "assets/laplace_strategy_input.schema.json",
    "assets/laplace_strategy_judgment.schema.json",
    "assets/customer_order_capacity_evidence.schema.json",
    "assets/theme_candidate_universe.schema.json",
    "assets/theme_research_packet.schema.json",
    "assets/scorecard_template.json",
    "assets/scorecard.schema.json",
    "assets/output_contract.schema.json",
    "scripts/data_layer.py",
    "scripts/data_contracts.py",
    "scripts/market_source_policy.py",
    "scripts/data_router.py",
    "scripts/build_falsification_dashboard.py",
    "scripts/a_share_capital_actions.py",
    "scripts/a_share_capital_action_quantifier.py",
    "scripts/build_theme_candidate_universe.py",
    "scripts/validate_theme_candidate_universe.py",
    "scripts/build_theme_research_packet.py",
    "scripts/validate_theme_research_packet.py",
    "scripts/run_theme_research_analysis.py",
    "scripts/technical_health.py",
    "scripts/build_comparison_report.py",
    "scripts/build_ai_review_packet.py",
    "scripts/build_ai_committee_packet.py",
    "scripts/build_ai_overlay_prompt.py",
    "scripts/build_research_debt_runbook.py",
    "scripts/data_consumption.py",
    "scripts/financial_periods.py",
    "scripts/render_research_report.py",
    "scripts/build_laplace_strategy_input.py",
    "scripts/build_laplace_strategy_prompt.py",
    "scripts/validate_comparison_report.py",
    "scripts/validate_laplace_strategy_input.py",
    "scripts/validate_laplace_strategy_judgment.py",
    "scripts/render_strategy_report.py",
    "scripts/validate_ai_research_dossier.py",
    "scripts/validate_ai_overlay.py",
    "scripts/validate_ai_review_outcome.py",
    "scripts/validate_agent_research_queue.py",
    "scripts/validate_research_delivery.py",
    "scripts/merge_ai_research_overlay.py",
    "scripts/validate_and_merge_ai_overlay.py",
    "scripts/execute_agent_research_queue.py",
    "scripts/serenity_chan_scorecard.py",
    "scripts/candidate_ranker.py",
    "scripts/validate_output_contract.py",
    "scripts/validate_output_contract_json.py",
    "scripts/run_static_evals.py",
    "scripts/build_agent_overlay_workspace.py",
    "scripts/run_real_data_smoke.py",
    "companion-skills/laplace-forecast/SKILL.md",
    "companion-skills/laplace-forecast/agents/openai.yaml",
    "companion-skills/laplace-forecast/references/first-order-lenses.md",
    "companion-skills/laplace-forecast/references/evidence-loop.md",
    "companion-skills/laplace-forecast/references/ledger-schema.md",
    "companion-skills/laplace-forecast/scripts/forecast_ledger.py",
]
BLOCKED_DISTRIBUTION_PATHS: Any = [".idea", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"]
MAX_SKILL_LINES: int = 500


def tracked_files(root: Path) -> set[str]:
    try:
        result: subprocess.CompletedProcess[str] = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"],
            check=False,
            capture_output=True,
            text=True,
        )
    except Exception:
        return set()
    if result.returncode != 0:
        return set()
    return {item for item in result.stdout.split("\0") if item}


def parse_frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---\n"):
        raise ValueError("SKILL.md must start with YAML frontmatter")
    end: Any = text.find("\n---", 4)
    if end == -1:
        raise ValueError("SKILL.md frontmatter closing delimiter missing")
    data: dict[str, str] = {}
    for line in text[4:end].strip().splitlines():
        if not line.strip() or line.startswith(" "):
            continue
        if ":" in line:
            k: Any
            v: Any
            k, v = line.split(":", 1)
            data[k.strip()] = v.strip().strip('"')
    return data


def main() -> None:
    root: Any = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
    skill: Any = root / "SKILL.md"
    errors: list[str] = []
    name: Any = ""
    if not skill.exists():
        errors.append(f"missing file: {skill}")
    else:
        text: Any = skill.read_text(encoding="utf-8")
        try:
            fm: Any = parse_frontmatter(text)
        except ValueError as exc:
            fm = {}
            errors.append(str(exc))
        name = fm.get("name", "")
        description: Any = fm.get("description", "")
        if not name:
            errors.append("name is required")
        if name and not NAME_RE.match(name):
            errors.append("name must be lowercase letters/numbers/hyphens")
        if not description:
            errors.append("description is required")
        if len(description) > 1024:
            errors.append(f"description too long: {len(description)}")
        unexpected_keys: Any = sorted(set(fm) - ALLOWED_FRONTMATTER_KEYS)
        if unexpected_keys:
            errors.append(f"unexpected frontmatter keys: {', '.join(unexpected_keys)}")
        line_count: int = len(text.splitlines())
        if line_count < 80:
            errors.append("SKILL.md has too few lines; upload may have collapsed newlines")
        if line_count > MAX_SKILL_LINES:
            errors.append(f"SKILL.md too long for progressive disclosure: {line_count} lines > {MAX_SKILL_LINES}")
    for sub in REQUIRED_DIRS:
        if not (root / sub).exists():
            errors.append(f"missing directory: {sub}")
    for file_name in REQUIRED_FILES:
        if not (root / file_name).exists():
            errors.append(f"missing file: {file_name}")
        elif file_name.endswith(".schema.json"):
            try:
                schema_payload: Any = json.loads((root / file_name).read_text(encoding="utf-8"))
            except Exception as exc:
                errors.append(f"invalid JSON schema file {file_name}: {exc}")
            else:
                if not isinstance(schema_payload, dict):
                    errors.append(f"schema file {file_name} must contain a JSON object")
                if isinstance(schema_payload, dict) and schema_payload.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
                    errors.append(f"schema file {file_name} must declare JSON Schema draft 2020-12")
                if isinstance(schema_payload, dict) and "type" not in schema_payload:
                    errors.append(f"schema file {file_name} must declare a root type")
        elif file_name.endswith(".json"):
            try:
                json.loads((root / file_name).read_text(encoding="utf-8"))
            except Exception as exc:
                errors.append(f"invalid JSON in {file_name}: {exc}")
    tracked: set[str] = tracked_files(root)
    for file_name in tracked:
        parts: set[str] = set(Path(file_name).parts)
        if any(blocked in parts for blocked in BLOCKED_DISTRIBUTION_PATHS):
            errors.append(f"distribution artifact is tracked and should be removed: {file_name}")
    script_dirs: list[Any] = [root / "scripts"]
    companion_root: Any = root / "companion-skills"
    if companion_root.exists():
        script_dirs.extend(sorted(companion_root.glob("*/scripts")))
    for scripts_dir in script_dirs:
        if scripts_dir.exists():
            for script in sorted(scripts_dir.glob("*.py")):
                text = script.read_text(encoding="utf-8", errors="replace")
                if len(text.splitlines()) < 5 and script.stat().st_size > 200:
                    errors.append(f"{script.relative_to(root)} appears newline-collapsed")
                try:
                    compile(text, str(script), "exec")
                except Exception as exc:
                    errors.append(f"Python syntax error in {script.relative_to(root)}: {exc}")
    if errors:
        for e in errors:
            print("ERROR:", e)
        raise SystemExit(1)
    print(f"OK: {name}")


if __name__ == "__main__":
    main()
