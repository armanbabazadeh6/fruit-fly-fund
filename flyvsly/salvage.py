"""Finish a live session that was killed rather than stopped.

A clean stop runs `_finalise`, which writes `recording.json` and `manifest.json` from state
that lived in memory: the equity curves, the trade list, the arm descriptions. A kill — Docker
going down, a power cut, `docker rm -f` — takes all of that with it, and because the app lists
runs by their manifest, the session becomes invisible in the browser even though every traded
bar is on disk.

It is not lost, though. The session wrote its observations and a checkpoint as it traded, so
everything a recording needs can be rebuilt from them: the bars, the curves, the fills, the
fees. What cannot be rebuilt is whatever only lived in memory, and this module says exactly
which parts those are rather than dressing a guess up as a recording.
"""

import dataclasses
import json
from decimal import Decimal
from pathlib import Path

from .arena import DISCLAIMERS, SCHEMA_VERSION, arm_settings, personas_for
from .benchmark import buy_and_hold, cash
from .config import ArenaRules, MarketSpec
from .market import Season
from .telemetry import summarise_curve, write_jsonl, write_recording

# The rules a live session runs unless it was told otherwise. Only used when the checkpoint
# predates the metadata being written down, and always reported as rebuilt.
LIVE_DEFAULTS = {
    "engine": "neural",
    "kind": "competition",
    "bar_seconds": 60,
    "warmup_bars": 120,
}


def _bars_from_observations(observations) -> tuple[list[float], list[int]]:
    closes, times = [], []
    for observation in observations:
        market = observation.get("market") or {}
        mid = market.get("mid")
        if mid is None:
            bid, ask = Decimal(str(market.get("bid", 0))), Decimal(str(market.get("ask", 0)))
            mid = float((bid + ask) / 2)
        closes.append(float(mid))
        times.append(int(observation["t"]))
    return closes, times


def _trades_for(arm_id: str, observations) -> list[dict]:
    trades = []
    for observation in observations:
        record = (observation.get("arms") or {}).get(arm_id) or {}
        execution = record.get("execution") or {}
        fill = execution.get("fill")
        if not fill:
            continue
        signal = record.get("signal") or {}
        memory = signal.get("memory") or {}
        trades.append(
            {
                "i": observation["i"],
                "t": observation["t"],
                "side": (record.get("decision") or {}).get("side"),
                "product": observation.get("product"),
                "base_size": fill.get("base"),
                "price": fill.get("price"),
                "quote_size": fill.get("quote"),
                "fee": fill.get("fee"),
                "reason": execution.get("reason"),
                "equity_after": (record.get("portfolio") or {}).get("equity"),
                "memory_changed_edges": memory.get("changed_edges"),
            }
        )
    return trades


def _arm_summary(arm_id: str, observations, initial: float, rules, season) -> dict:
    curve = [float(((o.get("arms") or {}).get(arm_id) or {}).get("portfolio", {}).get("equity", initial)) for o in observations]
    last = ((observations[-1].get("arms") or {}).get(arm_id) or {}) if observations else {}
    portfolio = last.get("portfolio") or {}
    signal = last.get("signal") or {}
    exposure = sum(
        1
        for o in observations
        if float((((o.get("arms") or {}).get(arm_id) or {}).get("portfolio") or {}).get("positions", {}).get(season.spec.product, 0) or 0) > 0
    )
    fills = int(portfolio.get("fills", 0) or 0)
    fees = Decimal(str(portfolio.get("fees_paid", "0") or "0"))
    trades = _trades_for(arm_id, observations)
    return {
        "curve": curve,
        **summarise_curve(curve, initial),
        "fills": fills,
        "vetoes": int(portfolio.get("vetoes", 0) or 0),
        "blocked_bars": 0,
        "fees_paid": str(fees),
        "halted": portfolio.get("halted"),
        "exposure_bars": exposure,
        "trades": trades,
        "deployment": {
            "bars": season.bars,
            "bars_holding": exposure,
            "holding_fraction": round(exposure / season.bars, 4) if season.bars else 0.0,
            "order_limit_usdc": str(rules.order_limit),
            "daily_order_limit": rules.daily_orders,
            "cooldown_seconds": rules.interval_seconds,
        },
        "final_memory": signal.get("memory"),
    }


def salvage_run(run_dir, force: bool = False) -> dict:
    """Rebuild a killed live session's recording from its checkpoint and observations.

    Refuses to touch a run that already finished: a real recording from a clean stop is
    evidence, and this is a reconstruction.
    """
    run_dir = Path(run_dir)
    checkpoint_path = run_dir / "live_state.json"
    observations_path = run_dir / "observations.jsonl"
    if (run_dir / "recording.json").exists() and not force:
        raise FileExistsError(f"{run_dir.name} already has a recording.json; pass force to replace it")
    if not checkpoint_path.exists() or not observations_path.exists():
        raise FileNotFoundError(f"{run_dir.name} has no checkpoint and observations to salvage")

    checkpoint = json.loads(checkpoint_path.read_text())
    observations = [json.loads(line) for line in observations_path.read_text().splitlines() if line.strip()]
    if not observations:
        raise ValueError(f"{run_dir.name} traded no bars, so there is nothing to reconstruct")

    rules_data = checkpoint.get("rules")
    rules = ArenaRules(**rules_data).validate() if rules_data else ArenaRules().validate()
    spec = MarketSpec(
        kind="coinbase",
        product=str(checkpoint.get("product", LIVE_DEFAULTS["product"] if "product" in LIVE_DEFAULTS else "BTC-USDC")),
        bars=len(observations),
        bar_seconds=int(checkpoint.get("bar_seconds", LIVE_DEFAULTS["bar_seconds"])),
    )
    closes, times = _bars_from_observations(observations)
    # `Season` always keeps at least one warm-up bar in front of the tradable window, and a
    # live session's warm-up was never written down. One placeholder — the first observed
    # bar's own mid — keeps bar i meaning observation i, which is what the curves and the
    # benchmarks are aligned to.
    placeholder_close, placeholder_time = closes[0], times[0] - spec.bar_seconds
    season = Season(
        spec,
        [placeholder_close] + closes,
        [placeholder_time] + times,
        {
            "source": checkpoint.get("venue", "unknown"),
            "mode": "live",
            "rebuilt_from": "observations.jsonl",
            "note": (
                "bar mids exactly as they were observed live; the warm-up that preceded the "
                "session was not stored and one placeholder bar stands in for it"
            ),
        },
    )

    recorded_arms = checkpoint.get("arms")
    if recorded_arms:
        arms = recorded_arms
        arm_metadata = "recorded in the checkpoint"
    else:
        personas = personas_for(str(checkpoint.get("kind", LIVE_DEFAULTS["kind"])))
        arms = []
        for persona in personas:
            settings = arm_settings(rules, persona["learning"])
            arms.append(
                {
                    "id": persona["id"],
                    "name": persona["name"],
                    "role": persona["role"],
                    "role_label": persona["role_label"],
                    "tagline": persona["tagline"],
                    "accent": persona["accent"],
                    "accent_soft": persona["accent_soft"],
                    "detail": persona["detail"],
                    "learning": persona["learning"],
                    "starting_capital": str(rules.capital),
                    "starting_weights": None,
                    "starting_report": None,
                    "settings": dataclasses.asdict(settings),
                    "settings_signature": settings.signature(),
                    "backend": {"rebuilt": True, "label": "Rebuilt from the live defaults after a kill."},
                }
            )
        arm_metadata = "rebuilt from the live defaults: this checkpoint predates the metadata"

    initial = float(rules.capital)
    summary = {
        "bars": len(observations),
        "initial_capital": str(Decimal(rules.capital)),
        "duration_seconds": int(observations[-1]["t"]) - int(observations[0]["t"]) + spec.bar_seconds,
        "seconds_per_bar_mean": round(
            sum(float(((o.get("arms") or {}).get("gordon") or {}).get("compute_seconds", 0) or 0) for o in observations)
            / len(observations),
            4,
        ),
        "truncated": True,
        "arms": {arm["id"]: _arm_summary(arm["id"], observations, initial, rules, season) for arm in arms},
        "benchmarks": {
            "buy_and_hold": buy_and_hold(season, Decimal(rules.capital), Decimal(rules.paper_fee)),
            "cash": cash(season, Decimal(rules.capital)),
        },
    }
    on_id = arms[0]["id"]
    off_id = arms[1]["id"]
    delta = float(Decimal(str(summary["arms"][on_id]["final_equity"])) - Decimal(str(summary["arms"][off_id]["final_equity"])))
    summary["comparison"] = {
        "memory_on": on_id,
        "memory_off": off_id,
        "equity_delta_usdc": round(delta, 6),
        "return_delta_pct": round(summary["arms"][on_id]["return_pct"] - summary["arms"][off_id]["return_pct"], 6),
        "leader": on_id if delta > 0 else off_id if delta < 0 else "tie",
        "single_season_note": (
            "One session cannot distinguish a consistent result from one lucky path. "
            "Run several before reading anything into this number."
        ),
    }
    summary["salvaged"] = {
        "reason": "the session was killed before it could stop, so the recording was rebuilt",
        "from": ["live_state.json", "observations.jsonl"],
        "arm_metadata": arm_metadata,
        "checkpoint_status": checkpoint.get("status"),
        "bars_traded_at_kill": checkpoint.get("bars_traded"),
    }

    run = {
        "id": run_dir.name,
        "created": times[0],
        "label": checkpoint.get("label") or f"live session {run_dir.name}",
        "engine": str(checkpoint.get("engine", LIVE_DEFAULTS["engine"])),
        "repeat": 0,
        "bars": len(observations),
        "bar_seconds": spec.bar_seconds,
        "season": season.describe(),
        "rules": json.loads(json.dumps(dataclasses.asdict(rules), default=str)),
        "kind": str(checkpoint.get("kind", LIVE_DEFAULTS["kind"])),
        "exam_window": None,
        "reinforcement_mode": rules.reinforcement,
        "rule_preset": "upstream",
        "population": checkpoint.get("population"),
        "readout": None,
        "decoder_gate_required": rules.require_gate,
        "starting_conditions": checkpoint.get("starting_conditions"),
        "hardware": checkpoint.get("hardware"),
        "duration_seconds": summary["duration_seconds"],
        "seconds_per_bar_mean": summary["seconds_per_bar_mean"],
        "truncated": True,
        "wall_mode": (
            "live, salvaged: this session was killed mid-flight and the recording was rebuilt "
            "from the bars and checkpoint it had already written"
        ),
        "inputs_identical_every_bar": bool(
            all(o.get("same_neural_input_both_arms", True) for o in observations)
        ),
        "live": checkpoint.get("live") or {
            "product": spec.product,
            "bar_seconds": spec.bar_seconds,
            "warmup_bars": checkpoint.get("warmup_bars", LIVE_DEFAULTS["warmup_bars"]),
            "session_opened": checkpoint.get("session_opened"),
            "bars_traded": len(observations),
            "lag_seconds_at_end": checkpoint.get("lag_seconds"),
            "polls": checkpoint.get("polls"),
        },
        "salvaged": summary["salvaged"],
    }
    recording = {
        "schema": SCHEMA_VERSION,
        "run": run,
        "arms": arms,
        "season": {
            "describe": season.describe(),
            "provenance": season.provenance,
            "bars": [
                {
                    "t": season.timestamp(i),
                    "mid": season.mid(i),
                    "bid": str(season.quote(i).bid),
                    "ask": str(season.quote(i).ask),
                }
                for i in range(season.bars)
            ],
        },
        "observations": observations,
        "summary": summary,
        "disclaimers": DISCLAIMERS,
    }
    write_recording(run_dir / "recording.json", recording)
    write_recording(
        run_dir / "manifest.json",
        {
            "schema": SCHEMA_VERSION,
            "id": run_dir.name,
            "run": run,
            "arms": arms,
            "summary": summary,
            "disclaimers": DISCLAIMERS,
        },
    )
    write_jsonl(run_dir / "observations.jsonl", observations)
    checkpoint["status"] = "salvaged"
    checkpoint["salvaged_bars"] = len(observations)
    write_recording(checkpoint_path, checkpoint)
    return recording
