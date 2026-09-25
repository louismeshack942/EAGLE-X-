"""Execution Gate — the precision layers finally get a vote on real money.

The audit found the CF consulting exactly one precision layer before firing
(`truth_engine.proven_edges`) while eagle, organism, super_profit, bottom_up
and lightning were built, tested and never wired into the execution path.
They were advisory dashboards: beautiful, and structurally unable to stop a
stake. This module closes that gap.

Design rules, all deliberate:

- **Veto only, never promote.** Every layer here can REFUSE a play. None can
  create one. The squad's play selection and the truth gate still decide what
  is *eligible*; this decides whether an eligible play is *safe enough*. A
  layer that could promote would let a new subsystem invent trades the rest of
  the system never agreed to.

- **Exact contract, never family.** A verdict about "OVER" is not a verdict
  about "OVER 6". A layer that evaluated a different contract has no opinion on
  this play and must not be read as either approving or refusing it. This is
  not pedantry: the first implementation treated a layer's refusal of *its own
  separate pick* as a veto on this one, which blocked every play at every skew
  level -- a blanket ban wearing a gate's clothes. `TestGateDiscriminates`
  pins both directions.

- **Fail closed.** If a layer raises, the play is refused and the error is
  recorded. A gate that opens when it breaks is not a gate.

- **Advisory by default.** EXECUTION_GATE_MODE=off leaves behaviour exactly as
  it was before this module existed, so the change can be deployed and measured
  before it is trusted. Modes: off | advisory (log verdicts, fire anyway) |
  enforce (block).

- **One evaluation per symbol per scan.** The layers are asked once about a
  symbol, then every candidate play is judged against that answer, rather than
  re-running the whole stack per play.
"""

import logging
import os
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

MODES = ("off", "advisory", "enforce")


def mode() -> str:
    """off | advisory | enforce. Advisory is the default so this change can be
    deployed and measured before it is allowed to block anything."""
    m = os.environ.get("EXECUTION_GATE_MODE", "advisory").strip().lower()
    return m if m in MODES else "advisory"


def evaluate_symbol(symbol: str) -> dict:
    """Ask every layer about this symbol ONCE. Returns raw per-layer answers.

    Separated from per-play judging so a scan over six candidate plays costs
    one evaluation of each layer, not eighteen.
    """
    out: dict = {"symbol": symbol}

    # ---- bottom-up: the gate-first decision layer -------------------------
    try:
        from app.services.bottom_up import bottom_up_engine

        evaluated = bottom_up_engine.evaluate(symbol)
        out["bottom_up"] = {
            "candidates": {
                (c.get("contract"), c.get("barrier")): c
                for c in (evaluated.get("candidates") or [])
            }
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("execution gate: bottom-up evaluate failed")
        out["bottom_up"] = {"error": str(exc)}

    # ---- super_profit: seven-brain ensemble + uncertainty ceiling ---------
    try:
        from app.services.super_profit import super_profit_engine

        d = super_profit_engine.decide(symbol)
        out["super_profit"] = {
            "final": d.get("final"),
            "contract": d.get("contract"),
            "barrier": d.get("barrier"),
            "failed_gates": d.get("failed_gates") or [],
            "uncertainty": d.get("uncertainty"),
            "model_agreement": d.get("model_agreement"),
            "regime": d.get("regime"),
            "health": d.get("health"),
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("execution gate: super_profit evaluate failed")
        out["super_profit"] = {"error": str(exc)}

    # ---- eagle: entry precision band -------------------------------------
    try:
        from app.services.eagle import eagle_engine

        s = eagle_engine.strike(symbol)
        out["eagle"] = {
            "final": s.get("final"),
            "band": s.get("band"),
            "contract": s.get("contract"),
            "barrier": s.get("barrier"),
            "entry_precision_score": s.get("entry_precision_score"),
            "failed_gates": s.get("failed_gates") or [],
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("execution gate: eagle evaluate failed")
        out["eagle"] = {"error": str(exc)}

    # ---- risk guard: the layer that already had veto authority -----------
    try:
        from app.services.risk_guard import risk_guard

        out["risk"] = {"killed": bool(getattr(risk_guard, "killed", False)),
                       "mode": getattr(risk_guard, "mode", None)}
    except Exception as exc:  # noqa: BLE001
        logger.exception("execution gate: risk evaluate failed")
        out["risk"] = {"error": str(exc)}

    return out


def _judge(play: dict, context: dict) -> Tuple[dict, list]:
    """Judge one play against an already-computed symbol context."""
    contract = play.get("type")
    barrier = play.get("digit")
    layers: Dict[str, dict] = {}
    blockers: list = []

    # ---- bottom-up -------------------------------------------------------
    bu = context.get("bottom_up") or {}
    if bu.get("error"):
        layers["bottom_up"] = {"error": bu["error"]}
        blockers.append("bottom_up: layer error")
    else:
        cand = (bu.get("candidates") or {}).get((contract, barrier))
        if cand is None:
            layers["bottom_up"] = {"evaluated": False,
                                   "reason": "no candidate for this exact contract"}
            blockers.append("bottom_up: contract not evaluated")
        else:
            layers["bottom_up"] = {
                "evaluated": True,
                "decision": cand.get("decision"),
                "grade": cand.get("grade"),
                "score": cand.get("score"),
                "auto_executable": bool(cand.get("auto_executable")),
                "failed_gates": cand.get("decision_reasons") or [],
            }
            if cand.get("decision") != "PASS":
                blockers.append("bottom_up: " + ", ".join(
                    cand.get("decision_reasons") or ["rejected"]))
            elif not cand.get("auto_executable"):
                # PASS but below the auto-execute bar: real evidence, not yet
                # strong enough to spend money on unattended.
                blockers.append(
                    f"bottom_up: grade {cand.get('grade')} below auto-execute bar")

    # ---- super_profit ----------------------------------------------------
    sp = context.get("super_profit") or {}
    if sp.get("error"):
        layers["super_profit"] = {"error": sp["error"]}
        blockers.append("super_profit: layer error")
    else:
        same = (sp.get("contract") == contract and sp.get("barrier") == barrier)
        layers["super_profit"] = {
            "final": sp.get("final"), "same_contract": same,
            "uncertainty": sp.get("uncertainty"),
            "model_agreement": sp.get("model_agreement"),
            "failed_gates": sp.get("failed_gates") or [],
            "regime": sp.get("regime"), "health": sp.get("health"),
        }
        if same and sp.get("final") != "EXECUTE":
            blockers.append("super_profit: " + ", ".join(
                sp.get("failed_gates") or [str(sp.get("final"))]))

    # ---- eagle -----------------------------------------------------------
    eg = context.get("eagle") or {}
    if eg.get("error"):
        layers["eagle"] = {"error": eg["error"]}
        blockers.append("eagle: layer error")
    else:
        same = (eg.get("contract") == contract and eg.get("barrier") == barrier)
        layers["eagle"] = {
            "final": eg.get("final"), "band": eg.get("band"),
            "same_contract": same,
            "entry_precision_score": eg.get("entry_precision_score"),
            "failed_gates": eg.get("failed_gates") or [],
        }
        if same and eg.get("final") != "STRIKE":
            blockers.append("eagle: " + ", ".join(
                eg.get("failed_gates") or [str(eg.get("final"))]))

    # ---- risk ------------------------------------------------------------
    rk = context.get("risk") or {}
    if rk.get("error"):
        layers["risk"] = {"error": rk["error"]}
        blockers.append("risk: layer error")
    else:
        layers["risk"] = {"killed": rk.get("killed"), "mode": rk.get("mode")}
        if rk.get("killed"):
            blockers.append("risk: kill switch engaged")

    return layers, blockers


def check_play(play: dict, symbol: str, balance: float = 0.0,
               context: Optional[dict] = None) -> dict:
    """Run every precision layer against one eligible play.

    Returns {"allowed": bool, "mode": str, "layers": {...}, "blockers": [...]}.
    In advisory/off mode `allowed` is True but `blockers` still reports what
    *would* have stopped it. Pass a precomputed `context` (from
    `evaluate_symbol`) to judge many plays without re-running the layers.
    """
    if context is None:
        context = evaluate_symbol(symbol)
    layers, blockers = _judge(play, context)
    mode_name = mode()
    return {
        "symbol": symbol,
        "contract": play.get("type"),
        "barrier": play.get("digit"),
        "mode": mode_name,
        "layers": layers,
        "blockers": blockers,
        "would_block": bool(blockers),
        "allowed": True if mode_name != "enforce" else not blockers,
    }
