#!/usr/bin/env python3
"""EAGLE-X live preflight gate (READ-ONLY).

Checks the deployed server's actual state before an operator enables LIVE automation:

  /health                     - service up
  /api/status                 - data_source == deriv_live, oauth_configured, live feed state
  /auth/status                - authenticated session present
  /api/automation/status      - live_enabled master switch, automation state, kill switch

It NEVER changes any server state and NEVER sets EXECUTION_LIVE_ENABLED. It only prints
PASS/FAIL gates and a final GO/STOP verdict. A GO requires every gate to pass AND the
operator to confirm the PAPER-validation checklist via --paper-check.

Usage:
  python scripts/preflight_live.py --base http://localhost:8000 \
      --paper-check      # confirm the PAPER validation window completed cleanly
      --require-auth     # fail if /auth/status is not authenticated
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request


def _get(base: str, path: str) -> dict:
    with urllib.request.urlopen(f"{base}{path}", timeout=10) as r:
        return json.loads(r.read().decode())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000", help="Base URL of the deployed app")
    ap.add_argument("--paper-check", action="store_true",
                    help="Confirm the PAPER validation window completed with no anomalies")
    ap.add_argument("--require-auth", action="store_true",
                    help="Fail if no authenticated Deriv session")
    args = ap.parse_args()

    gates: list[tuple[str, bool, str]] = []

    # 1. Health
    try:
        h = _get(args.base, "/health")
        ok = h.get("status") == "ok"
        gates.append(("health", ok, "GET /health"))
    except Exception as e:  # noqa: BLE001
        gates.append(("health", False, f"unreachable: {e}"))

    # 2. Feed source / OAuth / connections
    try:
        s = _get(args.base, "/api/status")
        gates.append(("feed-source", s.get("data_source") == "deriv_live",
                      f"data_source={s.get('data_source')}"))
        gates.append(("oauth-configured", bool(s.get("oauth_configured")),
                      f"oauth_configured={s.get('oauth_configured')}"))
        conns = s.get("connection") or []
        live_conn = [c for c in conns if c.get("state") in ("connected", "connecting")]
        gates.append(("live-feed-connection", len(live_conn) > 0,
                      f"{len(live_conn)}/{len(conns)} connections"))
    except Exception as e:  # noqa: BLE001
        gates.append(("feed-source", False, f"unreachable: {e}"))

    # 3. Authentication (optional unless --require-auth)
    try:
        a = _get(args.base, "/auth/status")
        authed = bool(a.get("authenticated"))
        gates.append(("authenticated", authed if args.require_auth else True,
                      f"authenticated={authed}{' (required)' if args.require_auth else ''}"))
    except Exception as e:  # noqa: BLE001
        gates.append(("authenticated", False, f"unreachable: {e}"))

    # 4. Automation status / live master switch
    try:
        st = _get(args.base, "/api/automation/status")
        live_enabled = bool(st.get("live_enabled"))
        state = st.get("state", "OFF")
        kill = bool(st.get("kill_switch"))
        gates.append(("live-enable-required", live_enabled,
                      f"live_enabled={live_enabled} (must be true BEFORE live trading)"))
        gates.append(("kill-switch-clear", not kill, f"kill_switch={kill}"))
        gates.append(("automation-state-armed",
                      state in ("MONITORING", "READY", "TRACKING"),
                      f"state={state}"))
    except Exception as e:  # noqa: BLE001
        gates.append(("automation", False, f"unreachable: {e}"))

    # 5. Operator PAPER-validation confirmation (gated deliberately)
    gates.append(("paper-validation-confirmed", args.paper_check,
                  "--paper-check provided by the operator (PAPER window clean, ledger/historical "
                  "reconciliation applied)"))

    print("\n=== EAGLE-X LIVE PREFLIGHT (read-only) ===")
    all_ok = True
    for name, ok, note in gates:
        print(f"[{'PASS' if ok else 'FAIL'}] {name:28s} {note}")
        all_ok = all_ok and ok

    verdict = "GO — full preflight passed. Operator may now enable LIVE on the server." if all_ok \
        else "STOP — not all gates passed. Do NOT enable live trading."
    print(f"\nVERDICT: {verdict}\n")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())