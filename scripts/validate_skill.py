#!/usr/bin/env python3
from __future__ import annotations
import py_compile
import re
import sys
from pathlib import Path

NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
ALLOWED_FRONTMATTER_KEYS = {"name", "description"}
REQUIRED_DIRS = ["references", "assets", "scripts", "evals", "agents", "examples"]
REQUIRED_FILES = [
    "references/01_data_first_market_router.md",
    "references/02_serenity_bottleneck_workflow.md",
    "references/03_fundamental_valuation_framework.md",
    "references/04_chan_technical_framework.md",
    "references/05_output_templates.md",
    "references/06_risk_compliance_no_guess.md",
    "assets/analysis_request.schema.json",
    "assets/evidence_ledger.schema.json",
    "assets/falsification_dashboard.schema.json",
    "assets/scorecard_template.json",
    "assets/scorecard.schema.json",
    "assets/output_contract.schema.json",
    "scripts/data_layer.py",
    "scripts/market_source_policy.py",
    "scripts/data_router.py",
    "scripts/build_falsification_dashboard.py",
    "scripts/serenity_chan_scorecard.py",
    "scripts/validate_output_contract.py",
    "scripts/validate_output_contract_json.py",
    "scripts/run_static_evals.py",
    "scripts/run_real_data_smoke.py",
]


def parse_frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---\n"):
        raise ValueError("SKILL.md must start with YAML frontmatter")
    end = text.find("\n---", 4)
    if end == -1:
        raise ValueError("SKILL.md frontmatter closing delimiter missing")
    data: dict[str, str] = {}
    for line in text[4:end].strip().splitlines():
        if not line.strip() or line.startswith(" "):
            continue
        if ":" in line:
            k, v = line.split(":", 1)
            data[k.strip()] = v.strip().strip('"')
    return data


def main() -> None:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
    skill = root / "SKILL.md"
    errors: list[str] = []
    name = ""
    if not skill.exists():
        errors.append(f"missing file: {skill}")
    else:
        text = skill.read_text(encoding="utf-8")
        try:
            fm = parse_frontmatter(text)
        except ValueError as exc:
            fm = {}
            errors.append(str(exc))
        name = fm.get("name", "")
        description = fm.get("description", "")
        if not name:
            errors.append("name is required")
        if name and not NAME_RE.match(name):
            errors.append("name must be lowercase letters/numbers/hyphens")
        if not description:
            errors.append("description is required")
        if len(description) > 1024:
            errors.append(f"description too long: {len(description)}")
        unexpected_keys = sorted(set(fm) - ALLOWED_FRONTMATTER_KEYS)
        if unexpected_keys:
            errors.append(f"unexpected frontmatter keys: {', '.join(unexpected_keys)}")
        if len(text.splitlines()) < 80:
            errors.append("SKILL.md has too few lines; upload may have collapsed newlines")
    for sub in REQUIRED_DIRS:
        if not (root / sub).exists():
            errors.append(f"missing directory: {sub}")
    for file_name in REQUIRED_FILES:
        if not (root / file_name).exists():
            errors.append(f"missing file: {file_name}")
    scripts_dir = root / "scripts"
    if scripts_dir.exists():
        for script in sorted(scripts_dir.glob("*.py")):
            text = script.read_text(encoding="utf-8", errors="replace")
            if len(text.splitlines()) < 5 and script.stat().st_size > 200:
                errors.append(f"{script.relative_to(root)} appears newline-collapsed")
            try:
                py_compile.compile(str(script), doraise=True)
            except Exception as exc:
                errors.append(f"Python syntax error in {script.relative_to(root)}: {exc}")
    if errors:
        for e in errors:
            print("ERROR:", e)
        raise SystemExit(1)
    print(f"OK: {name}")


if __name__ == "__main__":
    main()
