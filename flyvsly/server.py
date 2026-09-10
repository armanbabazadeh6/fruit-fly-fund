"""Local server: the browser experience, recorded runs, and a live arena feed.

Standard library only, bound to localhost. It serves the built front end, lists and returns
recordings, aggregates repeats, and streams a running arena over Server-Sent Events so the
two flies can be watched bar by bar.

Nothing here can place an order: the arena only ever constructs `PaperBroker`.
"""

import json
import mimetypes
import queue
import threading
import time
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .config import ArenaConfig, ArenaRules, MarketSpec
from .report import load_manifests, summarise

SSE_HEARTBEAT_SECONDS = 15
# Cap on replayed bars so a very long run cannot grow the server's memory without bound.
MAX_REPLAY_BARS = 4000


class RunHub:
    """One live run at a time, fan-out to any number of SSE listeners."""

    def __init__(self, runs: Path, data: str):
        self.runs = Path(runs)
        self.data = data
        self.lock = threading.Lock()
        self.subscribers: set[queue.Queue] = set()
        self.thread = None
        self.state = {"status": "idle"}
        self.queue = queue.Queue()
        # Enough state to let a browser that loads mid-run (or refreshes) join at the live
        # edge: the run's metadata, the season it is playing, both arm descriptions, and
        # every bar so far. Replay is what makes "watch it live" work from any tab.
        self.session: dict = {"bars": []}

    def subscribe(self) -> queue.Queue:
        channel: queue.Queue = queue.Queue(maxsize=256)
        with self.lock:
            self.subscribers.add(channel)
        return channel

    def replay(self) -> list:
        """Snapshot for a newly attached listener, taken under the same lock as broadcast."""
        with self.lock:
            return self._replay()

    def unsubscribe(self, channel):
        with self.lock:
            self.subscribers.discard(channel)

    def broadcast(self, kind: str, payload: dict):
        with self.lock:
            if kind == "run_starting":
                self.session = {"meta": payload, "bars": []}
            elif kind in ("season_ready", "arms_ready"):
                self.session[kind] = payload
            elif kind == "bar":
                bars = self.session.setdefault("bars", [])
                if len(bars) < MAX_REPLAY_BARS:
                    bars.append(payload)
            elif kind in ("run_failed", "batch_finished"):
                self.session["finished"] = kind
            replay = self._replay()
            channels = list(self.subscribers)
        for channel in channels:
            try:
                channel.put_nowait((kind, payload))
            except queue.Full:
                pass

    def clear_session(self):
        """Drop the replay buffer once a run is over.

        A finished run is served from its recording, not replayed as if it were still
        streaming. Without this, a page loaded after the run would attach to a run that
        had already stopped.
        """
        with self.lock:
            self.session = {"bars": []}

    def _replay(self) -> list:
        """Events a fresh listener needs, in order. Called with the lock held."""
        session = self.session
        if not session.get("meta"):
            return []
        events = [("run_starting", session["meta"])]
        if session.get("season_ready"):
            events.append(("season_ready", session["season_ready"]))
        if session.get("arms_ready"):
            events.append(("arms_ready", session["arms_ready"]))
        events.extend(("bar", bar) for bar in session.get("bars", []))
        return events

    def start_run(self, options: dict) -> dict:
        with self.lock:
            if self.thread and self.thread.is_alive():
                return {"started": False, "reason": "A run is already in progress."}
            self.state = {"status": "starting", "options": options}
            self.session = {"bars": []}
            self.thread = threading.Thread(
                target=self._worker, args=(options,), name="arena", daemon=True
            )
            self.thread.start()
        return {"started": True, "options": options}

    def _worker(self, options: dict):
        # Everything, imports included, is inside the guard: a worker that dies before its
        # first statement used to leave the hub reporting "starting" forever with no error
        # anywhere, which is exactly as diagnosable as it sounds.
        try:
            from .arena import Arena
            from .market import build_season

            rules = ArenaRules(
                capital=options.get("capital", "100"),
                order_limit=options.get("order_limit", "10"),
                daily_orders=int(options.get("daily_orders", 24)),
                require_gate=bool(options.get("require_gate", True)),
                reinforcement=str(options.get("reinforcement", "pnl")),
                neural_ms=float(options.get("neural_ms", 500)),
                decoder_threshold_hz=float(options.get("decoder_threshold_hz", 2)),
            ).validate()
            repeats = int(options.get("repeats", 1))
            label = options.get("label") or f"{options.get('engine', 'neural')}:web"
            for repeat in range(repeats):
                if options.get("market", "synthetic") == "synthetic":
                    spec = MarketSpec(
                        kind="synthetic",
                        bars=int(options.get("bars", 48)),
                        seed=int(options.get("seed", 7)) + repeat * 7919,
                    )
                else:
                    spec = MarketSpec(
                        kind="coinbase",
                        bars=int(options.get("bars", 48)),
                        window_offset_bars=repeat * int(options.get("bars", 48)),
                    )
                config = ArenaConfig(
                    rules=rules,
                    market=spec,
                    engine=options.get("engine", "neural"),
                    label=label,
                    out=self.runs,
                )
                season = build_season(spec)
                arena = Arena(config, on_event=self._event, data_root=self.data)
                run_id = time.strftime("%Y%m%d-%H%M%S") + f"-live-r{repeat}"
                self.state = {"status": "running", "run_id": run_id, "repeat": repeat, "repeats": repeats}
                self.broadcast(
                    "run_starting",
                    {
                        "run_id": run_id,
                        "repeat": repeat,
                        "repeats": repeats,
                        "label": label,
                        "engine": config.engine,
                        "season": spec.describe(),
                        "bars": spec.bars,
                        "market_kind": spec.kind,
                        "options": {
                            "capital": str(rules.capital),
                            "order_limit": str(rules.order_limit),
                            "paper_fee": str(rules.paper_fee),
                            "slippage": str(rules.slippage),
                            "spread_limit": str(rules.spread_limit),
                            "daily_orders": rules.daily_orders,
                            "interval_seconds": rules.interval_seconds,
                            "decoder_threshold_hz": rules.decoder_threshold_hz,
                            "neural_ms": rules.neural_ms,
                        },
                    },
                )
                recording = arena.run(
                    run_id=run_id, season=season, repeat=repeat, out_root=self.runs
                )
                self.clear_session()
                self.state = {
                    "status": "finished",
                    "run_id": run_id,
                    "summary": recording["summary"],
                }
            self.broadcast("batch_finished", {"label": label})
        except Exception as error:
            self.clear_session()
            self.state = {
                "status": "failed",
                "error": f"{type(error).__name__}: {error}",
                "trace": traceback.format_exc()[-2000:],
            }
            self.broadcast("run_failed", self.state)

    def _event(self, kind, payload):
        self.broadcast(kind, payload)


def _json_default(value):
    return str(value)


class Handler(BaseHTTPRequestHandler):
    server_version = "flyvsly"
    hub: RunHub = None
    web: Path = None

    def log_message(self, fmt, *args):
        if "/api/" in (self.path or "") and self.command != "GET":
            return
        if (self.path or "").startswith("/api/run/stream"):
            return
        return

    # -- helpers ---------------------------------------------------------------------

    def _send(self, status, body: bytes, content_type: str, extra=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, payload, status=200):
        self._send(
            status,
            json.dumps(payload, default=_json_default).encode(),
            "application/json",
        )

    def _error(self, status, message):
        self._json({"error": message}, status)

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        return json.loads(self.rfile.read(length) or b"{}")

    # -- routes ----------------------------------------------------------------------

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/api/health":
            return self._json({"ok": True, "run": self.hub.state})
        if path == "/api/recordings":
            return self._json({"recordings": self._listing()})
        if path == "/api/report":
            return self._json(summarise(load_manifests(self.hub.runs)))
        if path == "/api/run/status":
            return self._json(self.hub.state)
        if path == "/api/run/stream":
            return self._stream()
        if path.startswith("/api/recordings/"):
            run_id = path.rsplit("/", 1)[-1]
            return self._recording(run_id)
        return self._static(path)

    def do_POST(self):
        path = self.path.split("?")[0]
        if path == "/api/runs":
            try:
                options = self._body()
            except ValueError as error:
                return self._error(400, f"Invalid JSON body: {error}")
            return self._json(self.hub.start_run(options))
        return self._error(404, "Unknown endpoint")

    # -- implementations -------------------------------------------------------------

    def _listing(self):
        out = []
        for manifest in load_manifests(self.hub.runs):
            summary = manifest.get("summary") or {}
            arms = summary.get("arms") or {}
            benchmarks = summary.get("benchmarks") or {}
            out.append(
                {
                    "id": manifest["id"],
                    "label": manifest["run"].get("label"),
                    "engine": manifest["run"].get("engine"),
                    "season": manifest["run"].get("season"),
                    "market": (manifest["run"].get("season") or "").split(":")[0],
                    "bars": summary.get("bars"),
                    "created": manifest["run"].get("created"),
                    "duration_seconds": summary.get("duration_seconds"),
                    "seconds_per_bar_mean": summary.get("seconds_per_bar_mean"),
                    "truncated": summary.get("truncated", False),
                    "inputs_identical_every_bar": manifest["run"].get(
                        "inputs_identical_every_bar"
                    ),
                    "returns": {
                        "gordon": (arms.get("gordon") or {}).get("return_pct"),
                        "warren": (arms.get("warren") or {}).get("return_pct"),
                        "buy_and_hold": _bench_pct(benchmarks.get("buy_and_hold")),
                    },
                    "memory_changed_edges": (
                        (arms.get("gordon") or {}).get("final_memory") or {}
                    ).get("changed_edges"),
                }
            )
        return out

    def _recording(self, run_id):
        if "/" in run_id or run_id in ("", ".", ".."):
            return self._error(400, "Invalid recording id")
        path = Path(self.hub.runs) / run_id / "recording.json"
        if not path.exists():
            return self._error(404, "No such recording")
        return self._send(200, path.read_bytes(), "application/json")

    def _stream(self):
        channel = self.hub.subscribe()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        try:
            self._event("hello", {"status": self.hub.state})
            # Join a run already in progress, at the live edge.
            for kind, payload in self.hub.replay():
                self._event(kind, payload)
            last = time.monotonic()
            while True:
                try:
                    kind, payload = channel.get(timeout=1.0)
                    self._event(kind, payload)
                    last = time.monotonic()
                except queue.Empty:
                    if time.monotonic() - last > SSE_HEARTBEAT_SECONDS:
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                        last = time.monotonic()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            self.hub.unsubscribe(channel)

    def _event(self, kind, payload):
        data = json.dumps(payload, default=_json_default)
        self.wfile.write(f"event: {kind}\ndata: {data}\n\n".encode())
        self.wfile.flush()

    def _static(self, path):
        root = Path(self.web)
        if not root.exists():
            return self._send(
                200,
                _MISSING_WEB.encode(),
                "text/html; charset=utf-8",
            )
        candidate = (root / path.lstrip("/")).resolve()
        if root.resolve() not in candidate.parents and candidate != root.resolve():
            return self._error(403, "Path outside the web root")
        if candidate.is_dir():
            candidate = candidate / "index.html"
        if not candidate.exists():
            candidate = root / "index.html"
            if not candidate.exists():
                return self._error(404, "Not found")
        kind = mimetypes.guess_type(str(candidate))[0] or "application/octet-stream"
        return self._send(200, candidate.read_bytes(), kind)


_MISSING_WEB = """<!doctype html><meta charset="utf-8">
<title>Fly vs. Fly</title>
<body style="font:16px/1.6 ui-monospace,monospace;max-width:44rem;margin:12vh auto;padding:0 1.5rem;background:#0b0f14;color:#dbe7f3">
<h1>Fly vs. Fly</h1>
<p>The API is running, but the browser bundle has not been built.</p>
<pre style="background:#131a22;padding:1rem;border-radius:8px">cd web
npm install
npm run build</pre>
<p>Then reload this page. Endpoints: <code>/api/recordings</code>,
<code>/api/report</code>, <code>/api/run/stream</code>.</p>
</body>
"""


def serve(port=7777, runs=Path("runs"), web=Path("web/dist"), data="data", open_browser=True):
    hub = RunHub(Path(runs), data)
    handler = type("BoundHandler", (Handler,), {"hub": hub, "web": Path(web)})
    httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"Fly vs. Fly serving {url}  (recordings: {len(load_manifests(runs))})", flush=True)
    if open_browser:
        threading.Thread(target=lambda: (time.sleep(0.4), webbrowser.open(url)), daemon=True).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped", flush=True)
    finally:
        httpd.server_close()
    return 0


def _bench_pct(benchmark):
    if not benchmark:
        return None
    curve = benchmark.get("curve") or []
    initial = float(benchmark.get("initial_capital") or 1)
    return round((curve[-1] / initial - 1) * 100, 6) if curve and initial else None
