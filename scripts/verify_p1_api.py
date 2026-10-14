"""P1 HTTP smoke and restart-resume checks for an isolated local backend.

Start the backend with a fresh PIPELINE_ENV (for example p1-api-<date>).
The required --expected-env is checked from mapping history *before* a run
is triggered, to avoid writing to the default application scope.

  python scripts/verify_p1_api.py --expected-env p1-api-check smoke
  python scripts/verify_p1_api.py --expected-env p1-api-check start-resume
  # restart the backend without changing PIPELINE_ENV or DATABASE_URL
  python scripts/verify_p1_api.py --expected-env p1-api-check finish-resume --run-id <id>
"""

import argparse
import json
import os
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]


def _token() -> str:
    token = os.environ.get("SHARED_TOKEN")
    if token:
        return token
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("SHARED_TOKEN="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError("SHARED_TOKEN unavailable")


def _request(base: str, headers: dict, method: str, path: str, **kwargs) -> dict:
    response = requests.request(method, base + path, headers=headers, timeout=25, **kwargs)
    response.raise_for_status()
    return response.json()


def _wait(base: str, headers: dict, run_id: str, statuses: set[str], timeout: int = 120) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        detail = _request(base, headers, "GET", f"/api/runs/{run_id}")
        if detail.get("api_status") in statuses:
            return detail
        time.sleep(2)
    raise TimeoutError(f"run {run_id} did not reach {sorted(statuses)}")


def _check_scope(base: str, headers: dict, expected_env: str) -> dict:
    assert expected_env.startswith("p1-api-") and expected_env != "default"
    runtime = _request(base, headers, "GET", "/api/runtime")
    assert runtime["pipeline_env"] == expected_env, (runtime.get("pipeline_env"), expected_env)
    contracts = _request(base, headers, "GET", "/api/config/contracts")["contracts"]
    names = {c["name"] for c in contracts}
    assert {"default", "compact"} <= names
    for name in ("default", "compact"):
        _request(base, headers, "GET", f"/api/config/mapping?contract_name={name}")
        history = _request(base, headers, "GET", f"/api/config/mapping/history?contract_name={name}")["history"]
        assert history and all(row["pipeline_env"] == expected_env for row in history), (name, history)
        assert all(row["contract_name"] == name for row in history)
    return {c["name"]: c["version"] for c in contracts}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8123")
    parser.add_argument("--expected-env", required=True)
    parser.add_argument("mode", choices=["smoke", "start-resume", "finish-resume"])
    parser.add_argument("--run-id")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    headers = {"Authorization": f"Bearer {_token()}", "Content-Type": "application/json"}
    versions = _check_scope(base, headers, args.expected_env)

    if args.mode == "smoke":
        results = []
        for source_id, name in (("day1_clean.csv", "default"), ("compact_clean.csv", "compact")):
            run_id = _request(base, headers, "POST", "/api/runs",
                              json={"source_path": source_id, "contract_name": name})["run_id"]
            detail = _wait(base, headers, run_id, {"clean", "healed", "escalated"})
            assert detail["api_status"] == "clean", (source_id, detail.get("final_message"))
            assert detail["contract_name"] == name and detail["contract_version"] == versions[name]
            assert detail["pipeline_env"] == args.expected_env
            listing = _request(base, headers, "GET", f"/api/runs?contract_name={name}")["runs"]
            assert any(row["run_id"] == run_id for row in listing)
            results.append({"run_id": run_id, "contract": name, "status": detail["api_status"]})
        print(json.dumps({"ok": True, "mode": args.mode, "runs": results}))
    elif args.mode == "start-resume":
        run_id = _request(base, headers, "POST", "/api/runs",
                          json={"source_path": "day6_ambiguous_rename.csv", "contract_name": "default"})["run_id"]
        detail = _wait(base, headers, run_id, {"awaiting_approval", "healed", "escalated"})
        assert detail["api_status"] == "awaiting_approval", detail.get("final_message")
        print(json.dumps({"ok": True, "mode": args.mode, "run_id": run_id,
                          "status": detail["api_status"], "next": detail["next"]}))
    else:
        if not args.run_id:
            parser.error("finish-resume requires --run-id")
        detail = _request(base, headers, "GET", f"/api/runs/{args.run_id}")
        assert detail["api_status"] == "awaiting_approval" and detail["contract_name"] == "default"
        _request(base, headers, "POST", f"/api/runs/{args.run_id}/approve",
                 json={"note": "P1 restart-resume verification"})
        final = _wait(base, headers, args.run_id, {"healed", "escalated"})
        assert final["api_status"] == "healed", final.get("final_message")
        assert final["human_decision"] == "approve"
        assert final["mapping_version_written"] is not None
        print(json.dumps({"ok": True, "mode": args.mode, "run_id": args.run_id,
                          "status": final["api_status"], "mapping_version": final["mapping_version_written"]}))


if __name__ == "__main__":
    main()
