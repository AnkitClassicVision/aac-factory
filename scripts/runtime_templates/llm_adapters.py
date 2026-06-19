#!/usr/bin/env python3
"""
LLM harness adapters for compiled agents. Stdlib only.

v0.3 returns runtime-truth metadata, not just text. Certification must know the
requested model, the actual model when the provider exposes it, whether that
identity is verified, and whether usage is real or unknown.
"""

from __future__ import annotations

import json
import os
import subprocess
import urllib.request
from typing import Any

ADAPTER_VERSION = "0.3"

OPENAI_COMPAT = {
    "openai": ("https://api.openai.com/v1/chat/completions", "OPENAI_API_KEY"),
    "deepseek": ("https://api.deepseek.com/v1/chat/completions", "DEEPSEEK_API_KEY"),
    "mistral": ("https://api.mistral.ai/v1/chat/completions", "MISTRAL_API_KEY"),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
               ("GEMINI_API_KEY", "GOOGLE_API_KEY")),
}


def _unknown_usage(source: str = "unknown") -> dict[str, Any]:
    return {"tokens_in": "unknown", "tokens_out": "unknown", "source": source,
            "actual_usage_available": False}


def _result(*, text: str, requested_model: str, actual_model: str = "unknown",
            model_verified: bool = False, verification_source: str = "unverified",
            usage: dict[str, Any] | None = None, executor: str) -> dict[str, Any]:
    return {
        "text": text,
        "requested_model": requested_model or "unknown",
        "actual_model": actual_model or "unknown",
        "model_verified": bool(model_verified),
        "verification_source": verification_source,
        "usage": usage or _unknown_usage(),
        "executor": executor,
        "adapter_version": ADAPTER_VERSION,
    }


def _fake(model: str, executor: str) -> dict[str, Any]:
    section = os.environ.get("FACTORY_FAKE_SECTION")
    payload: dict[str, Any] = {
        "decision": os.environ.get("FACTORY_FAKE_DECISION", "fake_ok"),
        "confidence": float(os.environ.get("FACTORY_FAKE_CONFIDENCE", "0.95")),
        "rationale": os.environ.get("FACTORY_FAKE_RATIONALE", f"FACTORY_FAKE_LLM canned response (model={model or 'unset'})"),
        "citations": ["fake://test"],
    }
    if section is not None:
        payload["section"] = None if section == "__null__" else section
    if os.environ.get("FACTORY_FAKE_RAW_JSON"):
        payload = json.loads(os.environ["FACTORY_FAKE_RAW_JSON"])
    return _result(text=json.dumps(payload), requested_model=model, actual_model=model,
                   model_verified=True, verification_source="configured_fake",
                   usage=_unknown_usage("fake_unknown"), executor=executor)


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


def _usage_from_openai(out: dict) -> dict[str, Any]:
    u = out.get("usage") or {}
    if not u:
        return _unknown_usage()
    return {"tokens_in": u.get("prompt_tokens", "unknown"),
            "tokens_out": u.get("completion_tokens", "unknown"),
            "source": "api_usage", "actual_usage_available": True}


def _usage_from_anthropic(out: dict) -> dict[str, Any]:
    u = out.get("usage") or {}
    if not u:
        return _unknown_usage()
    return {"tokens_in": u.get("input_tokens", "unknown"),
            "tokens_out": u.get("output_tokens", "unknown"),
            "source": "api_usage", "actual_usage_available": True}


def _model_from_openai(out: dict, requested: str) -> tuple[str, bool, str]:
    actual = out.get("model") or "unknown"
    return actual, bool(actual != "unknown" and actual == requested), "api_response" if actual != "unknown" else "unverified"


def _chat_completions(url: str, key: str | None, model: str, system_prompt: str, user_payload: str,
                      executor: str) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    out = _post_json(url, headers,
                     {"model": model, "response_format": {"type": "json_object"},
                      "messages": [{"role": "system", "content": system_prompt},
                                   {"role": "user", "content": user_payload}]})
    actual, verified, source = _model_from_openai(out, model)
    return _result(text=out["choices"][0]["message"]["content"], requested_model=model,
                   actual_model=actual, model_verified=verified, verification_source=source,
                   usage=_usage_from_openai(out), executor=executor)


def complete_result(execution: dict, model: str, system_prompt: str, user_payload: str) -> dict[str, Any]:
    execution = execution or {}
    harness = execution.get("harness", "anthropic-api")
    model = model or "claude-sonnet-4-6"
    if os.environ.get("FACTORY_FAKE_LLM"):
        return _fake(model, harness)

    if model.startswith("ollama/") or harness in ("local-model", "ollama", "ollama-native"):
        tag = model.split("/", 1)[1] if "/" in model else model
        endpoint = (execution.get("endpoint") or execution.get("base_url") or "http://localhost:11434").rstrip("/")
        out = _post_json(f"{endpoint}/api/chat", {}, {
            "model": tag, "stream": False,
            "messages": [{"role": "system", "content": system_prompt},
                         {"role": "user", "content": user_payload}],
            "format": "json",
        })
        return _result(text=out["message"]["content"], requested_model=model,
                       actual_model=out.get("model", tag),
                       model_verified=bool(out.get("model") in (tag, model)),
                       verification_source="api_response" if out.get("model") else "configured_echo",
                       usage=_unknown_usage("ollama_unknown"), executor="ollama")

    if harness in ("local-openai-compatible", "openai-compatible-local") or model.startswith("local-openai-compatible/"):
        requested = model.split("/", 1)[1] if model.startswith("local-openai-compatible/") else model
        base = (execution.get("base_url") or execution.get("endpoint") or "http://localhost:8000/v1").rstrip("/")
        return _chat_completions(f"{base}/chat/completions", execution.get("api_key"), requested,
                                 system_prompt, user_payload, "local-openai-compatible")

    if harness == "claude-code-headless":
        r = subprocess.run(
            ["claude", "-p", "--model", model, "--output-format", "json",
             "--append-system-prompt", system_prompt, user_payload],
            capture_output=True, text=True, timeout=300)
        if r.returncode != 0:
            raise RuntimeError(f"claude CLI failed: {r.stderr[:300]}")
        text = r.stdout
        actual = "unknown"
        usage = _unknown_usage()
        verification = "unverified"
        try:
            payload = json.loads(r.stdout)
            text = payload.get("result") or payload.get("text") or payload.get("content") or r.stdout
            actual = payload.get("model") or payload.get("actual_model") or "unknown"
            if payload.get("usage"):
                u = payload["usage"]
                usage = {"tokens_in": u.get("input_tokens", u.get("prompt_tokens", "unknown")),
                         "tokens_out": u.get("output_tokens", u.get("completion_tokens", "unknown")),
                         "source": "cli_json", "actual_usage_available": True}
            verification = "cli_json" if actual != "unknown" else "unverified"
        except Exception:
            pass
        return _result(text=text, requested_model=model, actual_model=actual,
                       model_verified=bool(actual != "unknown" and actual == model),
                       verification_source=verification, usage=usage,
                       executor="claude-code-headless")

    if harness == "anthropic-api" or model.startswith("claude"):
        out = _post_json("https://api.anthropic.com/v1/messages",
                         {"x-api-key": _env("ANTHROPIC_API_KEY"), "anthropic-version": "2023-06-01"},
                         {"model": model, "max_tokens": 2048, "system": system_prompt,
                          "messages": [{"role": "user", "content": user_payload}]})
        actual = out.get("model") or model
        return _result(text="".join(b.get("text", "") for b in out.get("content", [])),
                       requested_model=model, actual_model=actual,
                       model_verified=bool(actual == model), verification_source="api_response",
                       usage=_usage_from_anthropic(out), executor="anthropic-api")

    provider = _provider_of(model)
    url, key_spec = OPENAI_COMPAT[provider]
    return _chat_completions(url, _env(key_spec), model, system_prompt, user_payload,
                             "openai-compatible")


def complete(execution: dict, model: str, system_prompt: str, user_payload: str) -> str:
    """Backward-compatible text-only helper."""
    return complete_result(execution, model, system_prompt, user_payload)["text"]
