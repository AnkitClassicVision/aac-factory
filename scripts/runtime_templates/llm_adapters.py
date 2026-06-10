#!/usr/bin/env python3
"""
LLM harness adapters for compiled agents. Stdlib only (urllib + subprocess) — no pip installs.

Harnesses (per node card execution.harness):
  anthropic-api         -> api.anthropic.com (ANTHROPIC_API_KEY)
  claude-code-headless  -> `claude -p` subprocess via your Claude login (no API key)
  openai-compatible     -> any /v1/chat/completions endpoint: OpenAI, DeepSeek, Mistral,
                           Gemini's compat endpoint (provider inferred from model id)
  ollama / local-model  -> local Ollama (http://localhost:11434)

FACTORY_FAKE_LLM=1 short-circuits every adapter with a deterministic canned response —
used by CI and shadow smoke runs; the run card still records which harness WOULD run.
"""

from __future__ import annotations

import json
import os
import subprocess
import urllib.request

OPENAI_COMPAT = {
    "openai": ("https://api.openai.com/v1/chat/completions", "OPENAI_API_KEY"),
    "deepseek": ("https://api.deepseek.com/v1/chat/completions", "DEEPSEEK_API_KEY"),
    "mistral": ("https://api.mistral.ai/v1/chat/completions", "MISTRAL_API_KEY"),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
               ("GEMINI_API_KEY", "GOOGLE_API_KEY")),
}


def _fake(model: str) -> str:
    return json.dumps({"decision": os.environ.get("FACTORY_FAKE_DECISION", "fake_ok"),
                       "confidence": float(os.environ.get("FACTORY_FAKE_CONFIDENCE", "0.95")),
                       "rationale": f"FACTORY_FAKE_LLM canned response (model={model or 'unset'})",
                       "citations": ["fake://test"]})


def _post_json(url: str, headers: dict, body: dict, timeout: int = 120) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _provider_of(model: str) -> str:
    m = model.lower()
    if m.startswith("gpt-") or m.startswith("o1") or m.startswith("o3"):
        return "openai"
    if m.startswith("gemini"):
        return "gemini"
    if m.startswith("mistral") or m.startswith("magistral") or m.startswith("devstral"):
        return "mistral"
    if m.startswith("deepseek"):
        return "deepseek"
    return "openai"


def _env(spec) -> str:
    keys = spec if isinstance(spec, tuple) else (spec,)
    for k in keys:
        v = os.environ.get(k)
        if v:
            return v
    raise RuntimeError(f"missing API key: set one of {keys}")


def complete(execution: dict, model: str, system_prompt: str, user_payload: str) -> str:
    if os.environ.get("FACTORY_FAKE_LLM"):
        return _fake(model)

    harness = (execution or {}).get("harness", "anthropic-api")
    model = model or "claude-sonnet-4-6"

    if model.startswith("ollama/") or harness in ("local-model", "ollama"):
        tag = model.split("/", 1)[1] if "/" in model else model
        out = _post_json("http://localhost:11434/api/chat", {}, {
            "model": tag, "stream": False,
            "messages": [{"role": "system", "content": system_prompt},
                         {"role": "user", "content": user_payload}],
            "format": "json",
        })
        return out["message"]["content"]

    if harness == "claude-code-headless":
        r = subprocess.run(
            ["claude", "-p", "--output-format", "text", "--append-system-prompt", system_prompt,
             user_payload],
            capture_output=True, text=True, timeout=300)
        if r.returncode != 0:
            raise RuntimeError(f"claude CLI failed: {r.stderr[:300]}")
        return r.stdout

    if harness == "anthropic-api" or model.startswith("claude"):
        out = _post_json("https://api.anthropic.com/v1/messages",
                         {"x-api-key": _env("ANTHROPIC_API_KEY"), "anthropic-version": "2023-06-01"},
                         {"model": model, "max_tokens": 2048, "system": system_prompt,
                          "messages": [{"role": "user", "content": user_payload}]})
        return "".join(b.get("text", "") for b in out.get("content", []))

    provider = _provider_of(model)
    url, key_spec = OPENAI_COMPAT[provider]
    out = _post_json(url, {"Authorization": f"Bearer {_env(key_spec)}"},
                     {"model": model, "response_format": {"type": "json_object"},
                      "messages": [{"role": "system", "content": system_prompt},
                                   {"role": "user", "content": user_payload}]})
    return out["choices"][0]["message"]["content"]
