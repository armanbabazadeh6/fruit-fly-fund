# Recording format

One recording is one season, one repeat, both flies. `runs/<id>/recording.json` holds
everything the browser renders; nothing is invented at display time. `manifest.json` is the
same run without the per-bar trail, for listings and reports. `observations.jsonl` is the
per-bar trail alone, one JSON object per line, for `grep` and spreadsheets.

Schema string: `flyvsly.recording/v1`. The front-end types are in `web/src/lib/types.ts`
and mirror this page.

```jsonc
{
  "schema": "flyvsly.recording/v1",
  "run": {
    "id": "20260910-143345-r0",
    "engine": "neural",              // "neural" | "procedural"
    "season": "coinbase:BTC-USDC:newest-0bars:bars=48",
    "bars": 48, "bar_seconds": 60,
    "rules": { /* the frozen rule set, identical for both arms */ },
    "starting_conditions": {
      "capital_usdc": "100", "paper_fee_per_side": "0.006",
      "fairness": {
        "differing_fields": ["learning"],
        "identical_fields": ["capital", "..."],
        "identical_fields_sha256": "..."
      }
    },
    "hardware": { "machine": "arm64", "cpu_count": 8, "python": "3.12.13",
                  "gpu_acceleration": "none: the vendored engine integrates on the CPU only",
                  "data_manifest": { "release": "MaleCNS v1.0", "neurons": 166700,
                                     "edges": 25582938 } },
    "duration_seconds": 412.7, "seconds_per_bar_mean": 8.6,
    "inputs_identical_every_bar": true,
    "wall_mode": "accelerated replay: one completed market bar per observation, ..."
  },

  "arms": [ /* two entries; only `learning` and its consequences differ */ {
    "id": "gordon", "name": "Gordon Flykko", "learning": true,
    "role_label": "Memory updates ON", "accent": "#ffb454",
    "settings": { /* full upstream Settings, incl. learning */ },
    "settings_signature": "<hash>",
    "backend": {
      "signal_source": "neural",
      "label": "MaleCNS v1.0 retained graph, 166,700 neurons, 25,582,938 connections",
      "neurons": 166700, "directed_edges": 25582938, "plastic_edges": 7835,
      "memory_updates_applied": true,
      "decoder_cells": { "left": ["..."], "right": ["..."], "gate": ["..."] },
      "memory_rule": { /* trace constants, bounds, gain, citation */ },
      "vision": { /* mapping counts, projection confidence, assumptions */ },
      "claims": "..."
    }
  }],

  "season": {
    "describe": "coinbase:BTC-USDC:newest-0bars:bars=48",
    "provenance": { "source": "coinbase-public-candles",
                    "window_start": "2022-07-13T16:00:00Z",
                    "series_sha256": "...", "cache_file": "data/markets/...json",
                    "disclaimer": "..." },
    "bars": [{ "t": 1657728060, "mid": 19755.0, "bid": "19750.06", "ask": "19759.94" }]
  },

  "observations": [{
    "i": 0, "t": 1657728060, "product": "BTC-USDC",
    "market": { "bid": "19750.06", "ask": "19759.94", "mid": 19755.0 },
    "frame_sha256": "...",              // the RGB chart handed to both flies
    "same_frame_both_arms": true,
    "same_neural_input_both_arms": true, // compares each arm's own input hash
    "arms": {
      "gordon": {
        "signal": {                      // neural shape
          "signal_source": "neural", "side": "BUY", "seconds": 0.5,
          "left_hz": 36.0, "right_hz": 50.0, "difference_hz": 14.0, "gate_spikes": 12,
          "stimulus": "aversive", "stimulus_ms": 200.0,
          "reward_spikes": 0, "aversive_spikes": 55, "KC_spikes": 3036,
          "total_spikes": 472392, "brain_ms": 500.0, "compute_seconds": 4.2,
          "spike_sha256": "...", "input_sha256": "...",
          "memory": { "enabled": true, "model": "stonkfly-dual-compartment-v1",
                      "plastic_edges": 7835, "changed_edges": 2665,
                      "mean_efficacy": 1.00266, "minimum_efficacy": 0.98, "sha256": "..." }
        },
        "decision": { "side": "BUY", "explanation": {
          "kind": "neural-threshold",
          "rule": "Fixed decoder. BUY requires mean(right DNp20) − mean(left DNp20) ≥ +2.00 Hz ...",
          "measured": { "dnp20_left_hz": 36.0, "dnp20_right_hz": 50.0,
                        "difference_hz": 14.0, "gate_spikes": 12, "threshold_hz": 2.0,
                        "seconds": 0.5 },
          "steps": ["Mean right DNp20 firing: 50.000 Hz over 0.500 s of neural time", "..."],
          "result": "BUY", "engineered_interface": true, "note": "..." } },
        "execution": { "status": "FILLED",
          "plan": { "base_size": "...", "limit_price": "...", "fee_ceiling": "...",
                    "observed_bid": "...", "observed_ask": "...",
                    "client_order_id": "...", "order_type": "limit_limit_fok" },
          "fill": { "base": "...", "quote": "...", "fee": "...", "price": "..." },
          "slippage_limit": "0.005", "fee_rate": "0.006",
          "explanation": ["Observed book: ...", "Guard sized a price-bounded order: ...",
                          "Fee ceiling reserved before sending: ...", "Paper fill: ..."] },
        "portfolio": { "cash": "...", "positions": { "BTC-USDC": "..." }, "equity": "...",
                       "return_pct": -0.31, "fees_paid": "...", "fills": 4,
                       "vetoes": 34, "halted": null },
        "stimulus": { "kind": "aversive", "delta_usdc": "-0.13" },
        "compute_seconds": 4.2
      },
      "warren": { /* same shape; warren.signal.memory.enabled is false */ }
    }
  }],

  "summary": {
    "bars": 48, "initial_capital": "100",
    "arms": {
      "gordon": {
        "final_equity": 99.71, "return_pct": -0.29, "max_drawdown_pct": 0.29,
        "path_volatility": 0.11, "curve": [100.0, 99.99, "..."],   // one point per bar
        "fills": 4, "vetoes": 34, "blocked_bars": 0, "fees_paid": "...", "halted": null,
        "exposure_bars": 5, "trades": [ /* see below */ ],
        "deployment": { "bars": 48, "bars_holding": 5, "holding_fraction": 0.078,
                        "order_limit_usdc": "10", "daily_order_limit": 24,
                        "cooldown_seconds": 60 },
        "final_memory": { /* last memory telemetry */ }
      },
      "warren": { "...": "same keys" }
    },
    "benchmarks": {
      "buy_and_hold": { "label": "Buy & hold", "curve": [99.35, "..."],
                        "initial_capital": "100", "base_size": "...",
                        "entry_price": "...", "entry_fee": "...", "fills": 1,
                        "detail": "One unrestricted fill at the first bar's ask ..." },
      "cash": { "curve": [100.0, "..."], "fills": 0 }
    },
    "comparison": {
      "memory_on": "gordon", "memory_off": "warren",
      "equity_delta_usdc": -0.0856, "return_delta_pct": -0.0856,
      "leader": "warren",
      "single_season_note": "One season cannot distinguish a consistent result from one lucky path. ..."
    }
  },

  "disclaimers": ["Paper trading only. ...", "..."]
}
```

## Trade record (`summary.arms[id].trades[]`)

```jsonc
{
  "i": 0, "t": 1657728060, "side": "BUY", "product": "BTC-USDC",
  "base_size": "0.00015858", "price": "61514.28", "quote_size": "9.7549345224",
  "fee": "0.0585296071344",
  "reason": "+6.000 Hz is beyond the ±2.00 Hz threshold with the gate satisfied: BUY",
  "equity_after": "99.9365956436656",
  "memory_changed_edges": 0
}
```

`reason` is the last step of the decision explanation, so the trade history and the
explainer cannot drift apart.

## Procedural recordings

`run.engine` is `"procedural"`, `arms[].backend.simulated` is `true`, and the per-bar
signal uses a different shape: `score`, `threshold`, `momentum`, `volatility`, `lookback`,
`simulated_memory_delta`, `base_score`, `stimulus`. It carries **no** spike counters, no
`spike_sha256` and no synaptic efficacies, so it cannot be mistaken for the fly on screen or
in a spreadsheet. The browser branches on `signal.signal_source` and shows a
"simulated signal" chip.

## Live streaming

`GET /api/run/stream` is Server-Sent Events with these event names:

| Event | Payload |
| --- | --- |
| `hello` | current hub status |
| `run_starting` | `run_id`, `engine`, `label`, `season`, `bars`, `repeat`, `repeats`, `options` |
| `season_ready` | `describe`, `provenance`, `bars[]` (the price series for the charts) |
| `arms_ready` | `arms[]` (the same arm metadata as a recording) |
| `bar` | `i`, `bars`, `t`, `market`, `frame_sha256`, `arms`, `same_neural_input_both_arms`, `elapsed` |
| `recording` | `run_id`, `summary` — the run finished and `GET /api/recordings/<id>` is now authoritative |
| `batch_finished` | `label` |
| `run_failed` | `error`, `trace` |

`bar.arms` has exactly the `ArmObservation` shape used inside a recording, so the browser
appends streamed bars to the same structures it renders for a finished run and swaps in the
recording's own summary when it lands. While a run is streaming, the scoreboard labels its
benchmark figures as provisional, because they are recomputed live from the streamed bars.
