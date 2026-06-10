#!/usr/bin/env python3
"""
Scan which model providers are actually available on this machine and build the
improvement-loop ladder from them. Runs when the user has no model preferences.

Detection (all offline-safe, no API calls except an optional local Ollama ping):
  anthropic / openai / gemini / mistral / deepseek -> provider env key present
  anthropic oauth -> `claude` CLI on PATH (claude-code-headless harness, no key needed)
  ollama -> endpoint reachable (2s timeout); locally installed models replace catalog stand-ins

Output: .factory-scan.json at the repo root (machine-local, gitignored).
  --write-config  seed factory.config.json model_preferences.ladder from the scan,
                  ONLY if the user has not set a ladder already (use --force to override).

Precedence reminder (factory_config.model_ladder): user ladder > scan cache > built-ins.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CATALOG = json.loads((Path(__file__).resolve().parent / "model_catalog.json").read_text(encoding="utf-8"))
SCAN_PATH = ROOT / ".factory-scan.json"
CONFIG_PATH = ROOT / "factory.config.json"


def env_present(spec) -> bool:
    keys = spec if isinstance(spec, list) else [spec]
    return any(os.environ.get(k) for k in keys)


def detect_providers() -> dict:
    providers = {}
    for name, spec in CATALOG["providers"].items():
        if name == "ollama":
            continue
        available = env_present(spec.get("env_key", []))
        via = "api-key" if available else None
        if name == "anthropic" and shutil.which(spec.get("oauth_cli", "claude")):
            available, via = True, (via or "oauth-cli")
        providers[name] = {"available": available, "via": via}
    # Ollama: ping locally, list installed models
    endpoint = CATALOG["providers"]["ollama"]["endpoint"]
    installed: list[str] = []
    try:
        with urllib.request.urlopen(f"{endpoint}/api/tags", timeout=2) as r:
            installed = [m["name"] for m in json.loads(r.read()).get("models", [])]
        providers["ollama"] = {"available": True, "via": "local", "models": installed}
    except Exception:
        providers["ollama"] = {"available": False, "via": None, "models": []}
    return providers


def build_ladder(providers: dict) -> list[dict]:
    ladder = []
    for m in CATALOG["catalog"]:
        p = providers.get(m["provider"], {})
        if not p.get("available"):
            continue
        if m["provider"] == "ollama":
            continue  # replaced by actually-installed models below
        ladder.append({k: m[k] for k in ("id", "provider", "cost_rank", "tier") if k in m})
    for name in providers.get("ollama", {}).get("models", []):
        ladder.append({"id": f"ollama/{name}", "provider": "ollama", "cost_rank": 0, "tier": "local"})
    ladder.sort(key=lambda m: (m["cost_rank"], m["id"]))
    return ladder


def main() -> None:
    providers = detect_providers()
    ladder = build_ladder(providers)
    scan = {
        "scanned_with": "scan_models.py",
        "catalog_snapshot": CATALOG.get("scanned"),
        "providers": providers,
        "ladder": ladder,
        "note": "machine-local; gitignored. Re-run after adding API keys or Ollama models.",
    }
    SCAN_PATH.write_text(json.dumps(scan, indent=2) + "\n", encoding="utf-8")
    avail = [k for k, v in providers.items() if v.get("available")]
    print(f"providers available: {', '.join(avail) or 'none (ladder empty — set an API key or install Ollama)'}")
    print(f"ladder: {len(ladder)} model(s) -> {SCAN_PATH.name}")

    if "--write-config" in sys.argv:
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8")) if CONFIG_PATH.exists() else {}
        mp = cfg.setdefault("model_preferences", {})
        if mp.get("ladder") and "--force" not in sys.argv:
            print("factory.config.json already has a ladder — left untouched (use --force to replace).")
            return
        mp["ladder"] = ladder
        mp.setdefault("_ladder_source", f"scan_models.py {scan['catalog_snapshot']}")
        CONFIG_PATH.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
        print(f"wrote ladder ({len(ladder)} models) into factory.config.json")


if __name__ == "__main__":
    main()
