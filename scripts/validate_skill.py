#!/usr/bin/env python3
from __future__ import annotations
import re
import sys
from pathlib import Path

NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


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
    if not skill.exists():
        raise SystemExit(f"Missing {skill}")
    fm = parse_frontmatter(skill.read_text(encoding="utf-8"))
    errors: list[str] = []
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
    for sub in ["references", "assets", "scripts", "evals", "agents"]:
        if not (root / sub).exists():
            errors.append(f"missing directory: {sub}")
    if errors:
        for e in errors:
            print("ERROR:", e)
        raise SystemExit(1)
    print(f"OK: {name}")


if __name__ == "__main__":
    main()
