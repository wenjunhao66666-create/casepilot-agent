"""Replay synthetic cases against a running API; never store tokens or reply text."""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

AGENTS = {"general", "technical", "billing", "escalation"}


def load_cases(path: Path) -> list[dict]:
    cases = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(cases, list) or not cases:
        raise ValueError("case suite must be a non-empty array")
    seen = set()
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("each case must be an object")
        case_id = case.get("id", "")
        if not isinstance(case_id, str) or not re.fullmatch(r"[a-z0-9-]{1,48}", case_id) or case_id in seen:
            raise ValueError("case IDs must be unique lowercase identifiers")
        seen.add(case_id)
        if not isinstance(case.get("message"), str) or not case["message"].strip():
            raise ValueError("each case needs a message")
        for field in ("allowed_primary_agents", "required_agents"):
            values = case.get(field, [])
            if not isinstance(values, list) or any(not isinstance(value, str) or value not in AGENTS for value in values):
                raise ValueError("invalid agent expectation")
        tools = case.get("required_tools", [])
        if not isinstance(tools, list) or any(not isinstance(tool, str) for tool in tools):
            raise ValueError("invalid tool expectation")
        for flag in ("requires_knowledge", "requires_escalation"):
            if flag in case and not isinstance(case[flag], bool):
                raise ValueError("expectation flags must be booleans")
    return cases


def assess(case: dict, body: dict) -> list[str]:
    if not isinstance(body, dict):
        return ["response_not_object"]
    failures = []
    for field in ("request_id", "conv_id", "response"):
        if not isinstance(body.get(field), str) or not body[field].strip():
            failures.append("missing_" + field)
    primary = body.get("primary_agent") or body.get("agent_type")
    allowed = case.get("allowed_primary_agents", [])
    if allowed and primary not in allowed:
        failures.append("unexpected_primary_agent")
    agents = body.get("agent_types", [])
    tools = body.get("tools_used", [])
    if not isinstance(agents, list) or any(not isinstance(item, str) for item in agents):
        failures.append("invalid_agent_types")
        agents = []
    if not isinstance(tools, list) or any(not isinstance(item, str) for item in tools):
        failures.append("invalid_tools_used")
        tools = []
    if not set(case.get("required_agents", [])).issubset(agents):
        failures.append("missing_required_agent")
    if not set(case.get("required_tools", [])).issubset(tools):
        failures.append("missing_required_tool")
    if case.get("requires_knowledge") and body.get("knowledge_used") is not True:
        failures.append("knowledge_not_used")
    if case.get("requires_escalation") and body.get("escalated") is not True:
        failures.append("not_escalated")
    return failures


def percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower, upper = math.floor(position), math.ceil(position)
    return round(ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower), 2)


def post_chat(base_url: str, payload: dict, token: str, timeout: float) -> dict:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    request = Request(base_url.rstrip("/") + "/chat", data=json.dumps(payload).encode(), headers=headers)
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read())
    except HTTPError as error:
        raise RuntimeError("http_" + str(error.code)) from None
    except (URLError, TimeoutError, OSError):
        raise RuntimeError("transport_error") from None
    except (ValueError, UnicodeError):
        raise RuntimeError("invalid_json") from None


def run_cases(cases: list[dict], base_url: str, token: str = "", timeout: float = 65,
              check_idempotency: bool = False) -> tuple[list[dict], dict]:
    run_id = uuid.uuid4().hex[:12]
    rows = []
    for case in cases:
        payload = {"message": case["message"], "user_id": "demo-" + run_id,
                   "conv_id": str(uuid.uuid4()), "client_request_id": run_id + "-" + case["id"]}
        row = {"case_id": case["id"], "http_success": False, "failures": []}
        started = time.perf_counter()
        try:
            body = post_chat(base_url, payload, token, timeout)
            row["wall_ms"] = round((time.perf_counter() - started) * 1000, 2)
            row["http_success"] = True
            row["failures"] = assess(case, body)
            if isinstance(body, dict):
                row["primary_agent"] = body.get("primary_agent")
                row["request_id"] = body.get("request_id")
            if check_idempotency:
                repeated = post_chat(base_url, payload, token, timeout)
                row["idempotent"] = isinstance(body, dict) and repeated == body
                if not row["idempotent"]:
                    row["failures"].append("receipt_mismatch")
        except RuntimeError as error:
            # post_chat emits only fixed error categories, never response bodies.
            row["failures"].append(str(error))
        row["passed"] = not row["failures"]
        rows.append(row)
    latencies = [row["wall_ms"] for row in rows if row["http_success"]]
    passed = sum(row["passed"] for row in rows)
    summary = {"run_id": run_id, "time_utc": datetime.now(timezone.utc).isoformat(),
               "total": len(rows), "passed": passed,
               "contract_pass_rate": passed / len(rows) if rows else None,
               "http_successes": len(latencies), "latency_sample_count": len(latencies),
               "latency_scope": "first successful HTTP responses; excludes receipt checks",
               "p50_wall_ms": percentile(latencies, 0.5), "p95_wall_ms": percentile(latencies, 0.95),
               "idempotency_checked": check_idempotency}
    return rows, summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--cases", type=Path, default=Path(__file__).with_name("service_cases.json"))
    parser.add_argument("--output-dir", type=Path, default=Path("data/replays"))
    parser.add_argument("--timeout", type=float, default=65)
    parser.add_argument("--check-idempotency", action="store_true")
    args = parser.parse_args()
    parsed = urlparse(args.base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        parser.error("base URL must be HTTP(S) without embedded credentials")
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("timeout must be positive")
    try:
        cases = load_cases(args.cases)
    except (ValueError, OSError) as error:
        parser.error("invalid case file: " + type(error).__name__)
    rows, summary = run_cases(cases, args.base_url, os.getenv("CASEPILOT_API_TOKEN", ""),
                              args.timeout, args.check_idempotency)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    prefix = args.output_dir / summary["run_id"]
    prefix.with_suffix(".jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    prefix.with_suffix(".json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["passed"] == summary["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
