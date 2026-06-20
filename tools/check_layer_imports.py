"""Assert the dependency direction domain <- ports <- config (spec §5/§17).

Allowed top-level llm_manager.* roots per layer:
  domain : llm_manager.domain            (intra-domain ok; stdlib otherwise)
  ports  : llm_manager.domain, llm_manager.ports, llm_manager.registry
  config : llm_manager.domain, llm_manager.ports, llm_manager.registry, llm_manager.config
Exits 1 on any violation. This is the authoritative layer guard (ruff
banned-api is intentionally not used — it can't scope per layer).
"""

from __future__ import annotations

import ast
import pathlib
import sys

ROOT = pathlib.Path("src/llm_manager")
ALLOWED: dict[str, set[str]] = {
    "domain": {"llm_manager.domain"},
    "ports": {"llm_manager.domain", "llm_manager.ports", "llm_manager.registry"},
    "config": {
        "llm_manager.domain",
        "llm_manager.ports",
        "llm_manager.registry",
        "llm_manager.config",
    },
}


def _top(name: str) -> str:
    """'llm_manager.domain.device' -> 'llm_manager.domain'."""
    parts = name.split(".")
    return ".".join(parts[:2]) if len(parts) >= 2 else name


def _imports(src: str) -> list[str]:
    tree = ast.parse(src)
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.append(node.module)
    return out


def main() -> int:
    violations: list[str] = []
    for layer, allowed in ALLOWED.items():
        layer_dir = ROOT / layer
        if not layer_dir.is_dir():
            continue
        for path in layer_dir.rglob("*.py"):
            src = path.read_text(encoding="utf-8")
            for mod in _imports(src):
                if mod.startswith("llm_manager.") and _top(mod) not in allowed:
                    violations.append(
                        f"{path}: imports {mod} (layer={layer}, allowed={sorted(allowed)})"
                    )
    if violations:
        print("\n".join(violations))
        return 1
    print("OK: dependency direction clean across domain/ports/config")
    return 0


if __name__ == "__main__":
    sys.exit(main())
