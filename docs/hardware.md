# Hardware notes

Measured, not estimated. All numbers below come from `flyvsly doctor --measure` and the
recorded `run.hardware` blocks in `runs/`.

## Development machine: Apple M2 MacBook Air, 8 GB

| Step | Measured |
| --- | --- |
| `flyvsly prepare` (download 1.1 GB, verify, compile the retained graph) | 68.6 s, 1.33 GB peak RSS |
| Graph on disk | 239 MB (`data/graph.npz`), 324 MB of normalised intermediates, 1.6 GB total data dir |
| One full brain resident (`MemoryBrain`: 25.58 M weights + CSR targets, spikes, queues) | ~0.30 GB |
| Two brains, one per fly, both mid-season | ~0.6–0.7 GB peak RSS (0.76 GB measured including the graph load) |
| `FlyController` construction per fly | ~5 s (graph decompression + circuit identification) |
| One 500 ms neural observation | 2.2 s with the graph warm in page cache, 2–8 s cold, rising as more cells become active |
| 48-bar season, both flies, arms on two threads | ~5–11 min wall, `seconds_per_bar_mean` 5–17 s |
| Procedural demo, 480 bars, both arms | under 5 s total |

Memory is comfortably within 8 GB: the bind is **wall time**, not RAM. The engine is a
single-threaded C loop per fly, and the two flies run on separate threads, so 8 cores give
roughly a 2× speedup over sequential execution and nothing more.

`data/` and `runs/` are git-ignored. A 48-bar neural recording is about 450 KB, a 480-bar
procedural one about 2 MB, so recordings are cheap to keep and to publish.

## What we deliberately do not do on this machine

- **No GPU path.** The vendored kernel is `ctypes` → compiled C++ over NumPy arrays, CPU
  only. Having an RTX 2080 on the desktop changes nothing unless someone ports the
  integrator; `flyvsly doctor` reports `gpu_acceleration: not used` and the recordings say
  the same thing. Do not expect a speedup from the discrete GPU.
- **No full-graph test on every run.** `FLYVSLY_NEURAL_TEST=1 pytest -m slow` builds extra
  brains and takes about a minute; it is opt-in.
- **No 480-bar neural seasons by default.** The CLI default is 48 bars for the neural
  engine and 480 for the procedural one, which is a statement about what the laptop can do,
  not about what the experiment deserves.

## Moving to the desktop (32 GB, RTX 2080)

The project is portable: pure Python + a `-shared -fPIC` C++17 kernel, no macOS-specific
code, no platform-conditional behaviour, and every execution rule runs on a virtual clock
rather than the wall clock. Nothing depends on the machine that produced a recording except
the `run.hardware` block, which is provenance rather than input.

Checklist, in order:

1. **Verify the specs you were told.** `flyvsly doctor` prints platform, CPU count, RAM,
   Python and the `c++` it found. Confirm RAM with the OS, not with the sticker.
2. **Check the toolchain first.** Upstream's kernel build shells out to `c++`
   (`clang++`/`g++` both provide it). Windows without WSL has no `c++`; use WSL2 or Linux.
   Confirm `c++ --version` before installing anything else, then `flyvsly prepare` — it
   compiles the kernel on first use and caches the binary with its hash.
3. **Confirm the Python and wheel matrix.** Python 3.11+ with `numpy`, `pandas`, `pyarrow`
   and `Pillow`. On Linux and WSL these are wheels; there is no compilation step beyond the
   kernel. `flyvsly doctor` reports what resolved.
4. **Re-measure, do not assume.** `flyvsly doctor --measure` builds one brain and times one
   observation on that machine. Use `seconds_per_bar_mean` from a 4-bar run to extrapolate
   the season length you can afford.
5. **Then run longer seasons.** The knobs, in order of usefulness:
   `--bars` (linear cost), `--repeats` (repeat the season over a different window), and
   `--neural-ms` (changes the protocol — do not use it to compare against other runs).
6. **Do not touch the GPU flags.** There are none. If someone ports the integrator to CUDA
   later, it must produce identical spikes at the same 0.1 ms timestep for the comparison to
   remain valid, and that has to be tested against `spike_sha256`, not asserted.

## Reproducing the numbers in this repository

```sh
flyvsly prepare
flyvsly doctor --measure
flyvsly run --engine neural --market coinbase --bars 48 --repeats 3 --label season-coinbase-48
flyvsly report
```

Coinbase seasons are anchored to the newest completed candle on the public exchange
endpoint and cached under `data/markets/`, so re-running the same command on the desktop
replays the same bars even though the local clock, timezone and machine differ. A synthetic
season is generated from a SHA-256 counter stream, so it does not depend on the NumPy or
Python version either.
