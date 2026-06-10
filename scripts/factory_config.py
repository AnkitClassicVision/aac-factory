#!/usr/bin/env python3
"""
User preferences for the AAC Factory: factory.config.json at the repo root.

Precedence everywhere: per-node card values > workflow card values > factory.config.json
> built-in defaults. Generators stamp preferences into cards at creation; editing the
config later never silently mutates existing cards (the pipeline is additive).
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DEFAULTS: dict = {
    "execution_preferences": {
        "judgment_harness": "anthropic-api",
        "auth_mode": "api-key",
        "deterministic_harness": "python-deterministic",
    },
    "model_preferences": {
        "default_judgment_model": None,
        "ladder": [
            {"id": "claude-haiku-4-5-20251001", "cost_rank": 1, "tier": "fast", "provider": "anthropic"},
            {"id": "claude-sonnet-4-6", "cost_rank": 2, "tier": "balanced", "provider": "anthropic"},
            {"id": "claude-opus-4-8", "cost_rank": 3, "tier": "deep", "provider": "anthropic"},
        ],
        "allow_local_models": False,
        "local": {"provider": "ollama", "endpoint": "http://localhost:11434", "models": []},
    },
    "deploy_preferences": {"runtime_target": None},
}


def load_config() -> dict:
    cfg = json.loads(json.dumps(DEFAULTS))
    p = ROOT / "factory.config.json"
    if p.exists():
        try:
            user = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return cfg
        for section, values in user.items():
            if section.startswith("_") or not isinstance(values, dict):
                continue
            cfg.setdefault(section, {})
            for k, v in values.items():
                if not k.startswith("_"):
                    cfg[section][k] = v
    return cfg


def model_ladder(cfg: dict | None = None) -> list[dict]:
    """The improvement loop's challenger set.
    Precedence: user ladder (factory.config.json) > machine scan (.factory-scan.json,
    written by scan_models.py when the user has no preferences) > built-in defaults.
    Local models join (cost_rank 0) only when allowed/installed."""
    cfg = cfg or load_config()
    mp = cfg.get("model_preferences", {})
    ladder = list(mp.get("ladder") or [])
    if not ladder:
        scan_path = ROOT / ".factory-scan.json"
        if scan_path.exists():
            try:
                ladder = list(json.loads(scan_path.read_text(encoding="utf-8")).get("ladder") or [])
            except Exception:
                ladder = []
    if not ladder:
        ladder = list(DEFAULTS["model_preferences"]["ladder"])
    if mp.get("allow_local_models") and (mp.get("local") or {}).get("models"):
        local = mp["local"]
        for m in local["models"]:
            ladder.append({"id": f"{local.get('provider', 'local')}/{m}", "cost_rank": 0,
                           "tier": "local", "provider": local.get("provider", "local"),
                           "endpoint": local.get("endpoint")})
    return ladder


if __name__ == "__main__":
    print(json.dumps(load_config(), indent=2))
