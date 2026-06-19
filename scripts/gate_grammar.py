#!/usr/bin/env python3
"""
Executable routing and output-gate grammar for AAC Factory v0.3.

The point of this module is not to become a policy language. It is the small,
auditable subset the compiler can prove the runtime will execute. Anything that
claims to be executable but cannot be parsed here must fail compile instead of
silently becoming prose.
"""

from __future__ import annotations

import re
from typing import Any

ROUTE_RE = re.compile(
    r"^([A-Za-z_][\w.]*)\s*(==|=|!=|>=|<=|>|<)\s*(.+)$"
)
IN_RE = re.compile(r"^([A-Za-z_][\w.]*)\s+in\s+\[([^\]]*)\]$", re.I)
CALL_RE = re.compile(r"^([a-zA-Z_][\w]*)\((.*)\)$")

PATTERN_LIBRARY = {
    "email": r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-z]{2,}",
    "phone": r"\b\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}\b",
    "ssn": r"\b\d{3}-\d{2}-\d{4}\b",
    "research_filler": (
        r"\b(no new development|no material new development|unchanged status|"
        r"recap[- ]only|generic trend|unverifiable|junk corpus fragment|"
        r"signal today\s*:?)\b"
    ),
}

ADVISORY_PREFIXES = ("todo:", "n/a", "advisory:", "note:", "design-time:")


def _strip_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def _split_args(raw: str) -> list[str]:
    args: list[str] = []
    cur: list[str] = []
    quote = ""
    depth = 0
    for ch in raw:
        if quote:
            cur.append(ch)
            if ch == quote:
                quote = ""
            continue
        if ch in {'"', "'"}:
            quote = ch
            cur.append(ch)
            continue
        if ch in "([":
            depth += 1
            cur.append(ch)
            continue
        if ch in ")]":
            depth -= 1
            cur.append(ch)
            continue
        if ch == "," and depth == 0:
            args.append("".join(cur).strip())
            cur = []
            continue
        cur.append(ch)
    if cur or raw.strip() == "":
        args.append("".join(cur).strip())
    return args


def _get(data: dict[str, Any], field: str) -> Any:
    cur: Any = data
    for part in field.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


def _present(value: Any) -> bool:
    return value is not None and value != "" and value != [] and value != {}


def _coerce(value: str) -> Any:
    v = _strip_quotes(value)
    low = v.lower()
    if low == "null":
        return None
    if low == "true":
        return True
    if low == "false":
        return False
    try:
        return float(v) if "." in v else int(v)
    except ValueError:
        return v


def parse_condition(condition: str) -> dict[str, Any]:
    c = (condition or "").strip()
    if not c or c.lower() == "always":
        return {"type": "always"}
    m = IN_RE.match(c)
    if m:
        return {"type": "in", "field": m.group(1),
                "values": [_coerce(v) for v in _split_args(m.group(2)) if v.strip()]}
    m = ROUTE_RE.match(c)
    if m:
        return {"type": "compare", "field": m.group(1), "op": m.group(2), "value": _coerce(m.group(3))}
    raise ValueError(f"unparseable condition: {condition!r}")


def eval_condition(condition: str, output: dict[str, Any]) -> bool:
    parsed = parse_condition(condition)
    if parsed["type"] == "always":
        return True
    if parsed["type"] == "in":
        return _get(output, parsed["field"]) in parsed["values"]
    left = _get(output, parsed["field"])
    op = parsed["op"]
    right = parsed["value"]
    if op in ("=", "=="):
        return left == right or str(left).strip().lower() == str(right).strip().lower()
    if op == "!=":
        return not (left == right or str(left).strip().lower() == str(right).strip().lower())
    try:
        lnum = float(left)
        rnum = float(right)
    except (TypeError, ValueError):
        return False
    return {
        ">=": lnum >= rnum,
        "<=": lnum <= rnum,
        ">": lnum > rnum,
        "<": lnum < rnum,
    }[op]


def is_advisory_text(rule: str) -> bool:
    low = (rule or "").strip().lower()
    return not low or low.startswith(ADVISORY_PREFIXES)


def parse_gate_rule(rule: str) -> dict[str, Any]:
    r = (rule or "").strip()
    m = CALL_RE.match(r)
    if not m:
        raise ValueError(f"unparseable gate rule: {rule!r}")
    name = m.group(1)
    args = _split_args(m.group(2))
    if name not in {
        "required", "required_if", "not_null", "enum", "min", "max",
        "regex_absent", "source_required_if", "confidence_floor_by_action_consequence",
    }:
        raise ValueError(f"unknown gate rule {name!r}: {rule!r}")
    if name in {"required", "not_null"} and len(args) == 1:
        return {"name": name, "field": args[0]}
    if name == "required_if" and len(args) == 2:
        parse_condition(args[0])
        return {"name": name, "condition": args[0], "field": args[1]}
    if name == "enum" and len(args) == 2:
        vals_raw = args[1].strip()
        vals = vals_raw[1:-1] if vals_raw.startswith("[") and vals_raw.endswith("]") else vals_raw
        return {"name": name, "field": args[0], "values": [_coerce(v) for v in _split_args(vals) if v.strip()]}
    if name in {"min", "max"} and len(args) == 2:
        return {"name": name, "field": args[0], "value": float(_coerce(args[1]))}
    if name == "regex_absent" and len(args) == 2:
        pattern_name = _strip_quotes(args[1])
        if pattern_name not in PATTERN_LIBRARY:
            raise ValueError(f"unknown regex pattern {pattern_name!r}: {rule!r}")
        return {"name": name, "field": args[0], "pattern_name": pattern_name}
    if name == "source_required_if" and len(args) == 1:
        parse_condition(args[0])
        return {"name": name, "condition": args[0]}
    if name == "confidence_floor_by_action_consequence":
        return {"name": name, "args": args}
    raise ValueError(f"wrong arity for gate rule: {rule!r}")


def execute_gate(rule: str, output: dict[str, Any], *, context: dict[str, Any] | None = None) -> tuple[bool, str]:
    context = context or {}
    parsed = parse_gate_rule(rule)
    name = parsed["name"]
    if name == "required":
        ok = _present(_get(output, parsed["field"]))
    elif name == "not_null":
        ok = _get(output, parsed["field"]) is not None
    elif name == "required_if":
        ok = (not eval_condition(parsed["condition"], output)) or _present(_get(output, parsed["field"]))
    elif name == "enum":
        ok = _get(output, parsed["field"]) in parsed["values"]
    elif name == "min":
        try:
            ok = float(_get(output, parsed["field"])) >= parsed["value"]
        except (TypeError, ValueError):
            ok = False
    elif name == "max":
        try:
            ok = float(_get(output, parsed["field"])) <= parsed["value"]
        except (TypeError, ValueError):
            ok = False
    elif name == "regex_absent":
        target = jsonish(_get(output, parsed["field"]))
        ok = re.search(PATTERN_LIBRARY[parsed["pattern_name"]], target, re.I) is None
    elif name == "source_required_if":
        ok = (not eval_condition(parsed["condition"], output)) or bool(output.get("citations") or output.get("source_refs"))
    elif name == "confidence_floor_by_action_consequence":
        floor = context.get("confidence_floor", 0.6)
        try:
            ok = float(output.get("confidence") or 0) >= float(floor)
        except (TypeError, ValueError):
            ok = False
    else:  # pragma: no cover
        ok = False
    return ok, "pass" if ok else f"failed {rule}"


def jsonish(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        import json
        return json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        return str(value)


def normalize_gate_declaration(raw: Any) -> dict[str, Any]:
    if isinstance(raw, str):
        return {"gate_id": re.sub(r"[^a-z0-9]+", "_", raw.lower()).strip("_")[:60] or "gate",
                "gate_type": "deterministic", "certifying": True, "rule": raw,
                "failure_route": "refuse_sink"}
    if isinstance(raw, dict):
        rule = raw.get("rule") or raw.get("check") or raw.get("condition") or ""
        return {"gate_id": raw.get("gate_id") or raw.get("id") or "gate",
                "gate_type": raw.get("gate_type", "deterministic"),
                "certifying": bool(raw.get("certifying", True)),
                "rule": rule,
                "failure_route": raw.get("failure_route", "refuse_sink"),
                "evidence_required": raw.get("evidence_required", "compile_enforced")}
    raise ValueError(f"unsupported gate declaration: {raw!r}")
