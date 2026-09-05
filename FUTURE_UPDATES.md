# Future Updates Roadmap

Below is a prioritized roadmap based on the README, the two engines, and `run_benchmark.py`. Items are grouped by impact, with small fixes at the end.

## 1. Scientific rigor (most impactful)

The headline numbers in §4 of the README look hand-typed rather than auto-generated. For a benchmark to be credible, the table needs to come out of the run itself.

- **Auto-generate the results table and the "Results Summary" section** from `compute_statistics()` and inject them into the README (or write `output/results.md`). Otherwise the published numbers can drift from what the suite actually produces.
- **Run N trials (e.g. 5–10)** and report median + IQR, not a single run. Latency benchmarks are noisy; a one-shot table overstates determinism. Persist per-trial CSVs into `output/runs/<timestamp>/`.
- **Add a warm-up period** (e.g. discard the first 500 callbacks) to remove first-buffer jitter, then report stats on the steady-state window only.
- **Capture OS-level context** alongside engine metrics: CPU governor, process priority, nice/chrt, ASIO/WASAPI exclusive mode, and `defaultLowOutputLatency`. These dominate results more than the language choice.
- **Stratify the Python case by GC pressure** — currently it's "all churn, always." Add presets (none, light, heavy, extreme) so you can plot how latency scales with alloc rate.

## 2. Python engine — the heap‑churn methodology is weak

`churn_graph` is a local list that you immediately `del`. CPython's cyclic GC only collects unreachable cycles at the end of the callback, but more importantly, the objects are small dicts wrapping small numpy arrays — that's not where real GC pauses come from. To produce a more realistic "GC hazard" scenario:

- **Pin a longer-lived list and rotate references** through it so GC must traverse reachable+unreachable cycles each callback.
- **Vary the alloc size** (try a 1 MiB numpy buffer per node) — large object frees are what triggers major collections.
- **Disable the cyclic GC** at the start of `audio_callback` and re-enable at the end, and record the time spent inside `gc.collect()` (or `gc.get_stats()` deltas) as a separate channel.
- **Benchmark a "no-churn" Python baseline** so you can separate Python interpreter overhead from GC overhead.

## 3. Expand the language matrix

Two languages is a thin basis for a generalization. Add at least:

- **Rust with cpal** (the obvious modern zero-alloc competitor to C++).
- **C#/.NET 8 with NAudio or CoreAudio** — this is the most interesting comparison since .NET also has a GC and is widely used in audio plugins.
- **C++ with `std::vector`-based DSP** as a negative control to show that even C++ loses determinism if the hot path allocates.
- **Numba/Cython JIT'd Python** to quantify how much of the Python overhead is interpreter dispatch vs allocation.

## 4. Real-time engineering depth

- **Sweep block size** (32, 64, 128, 256, 512, 1024) and sample rate (44.1 / 48 / 96 kHz). The whole "sub-3 ms" framing depends on these parameters — show the relationship.
- **Add lock-free vs mutex-protected paths** in C++ and measure contention cost (e.g. `std::mutex` vs `std::atomic` vs SPSC ring buffer).
- **Add a SIMD variant** of the sine loop (`-mavx2 / -mfma`) and report the gain.
- **Add a multi-channel variant** (stereo, 5.1) — many plugins blow the deadline when channels grow.
- **Record the CPU core** the callback ran on (`GetCurrentProcessorNumberEx` / `sched_getcpu`) — core migration is a real source of jitter on Windows.
- **Drop `portaudio.dll` from the repo** and use a proper CMake/find module that links against the system PortAudio (or vcpkg/conan) so the build is reproducible across machines.
- **Promote the benchmark to a Google Benchmark or Catch2 harness** for the C++ side so you get per-test statistical reporting, instead of one giant `main()`.

## 5. Tooling & CI

- **Add a GitHub Actions matrix** that builds the C++ engine (MSVC, MinGW, clang) and runs both engines headless. Headless CI won't have a real audio device — make that explicit and add a `--mock` flag that drives the callback from a timer at the correct cadence without PortAudio, so unit tests can run anywhere.
- **Add pytest tests** for `run_benchmark.py` (CSV loader, percentile math, plot generation with matplotlib's Agg backend, no display required).
- **Add a Makefile / CMakeLists.txt** as the canonical build entry point. Right now the build command is duplicated in three places.
- **Add pre-commit** with `black`, `ruff`, `clang-format`, and a markdownlint pass for the README.
- **Containerize**: a Dockerfile with PortAudio + Python so reviewers can reproduce without Windows-specific DLLs.

## 6. README / documentation

- the README hard-codes Windows shell snippets (`.venv\Scripts\python.exe`). Add a POSIX variant (`.venv/bin/python`) or a small cross-platform bootstrap (`scripts/run.sh`, `scripts/run.ps1`).
- **Replace the ASCII architecture diagram** with a Mermaid diagram (renders natively on GitHub) and add a sequence diagram for one callback invocation.
- **Add an "Interpretation" section** that explicitly calls out what the experiment does not prove (e.g. it doesn't measure plugin host overhead, DSP graphs, or disk streaming).
- **Cite prior art**: Boerner/Lee "Real-Time Programming in C++", the JACK latency FAQ, and the PortAudio underflow docs.
- the `stack.cpp` at the repo root looks like leftover scratch from learning `std::stack` — it's unrelated to the audio benchmark. Either delete it or move it under `cpp/exercises/` with a header explaining it's a tutorial file, so the repo top-level tells a single coherent story.

## 7. Quick housekeeping

- **.gitignore issues**: `cpp_jitter_test.exe` (root), `cpp/cpp_jitter_test.exe`, `__pycache__/`, `build/`, and `output/` binaries appear to be tracked or unfiltered. Commit only source; add `*.exe`, `*.dll`, `__pycache__/`, `build/`, `output/*.csv`, `output/*.png`, `*.pyc` to `.gitignore`.
- **`PY_METRICS_PATH`** is computed relative to `SCRIPT_DIR` correctly, but the C++ engine writes `cpp_metrics.csv` relative to CWD (not relative to the source file). That breaks if you run the `.exe` from a different directory. Make it resolve relative to the executable's location.
- **The runner dashboard thread joins with `timeout=1.0`** but doesn't observe the join — if the dashboard thread is stuck in a sleep, the final newline may be lost. Use `join()` and a small sentinel write.
- **`run_benchmark.py` reads `DEADLINE_US` and `DEADLINE_MS`** but they are never defined in that file. They must be globals that got lost in a refactor — define them at module top.
- Both engines print the same "Stream active. Running 10-second benchmark..." banner at the same time as the runner's progress bar, which causes interleaved output on Windows console. Consider silencing the engine stdout while the runner dashboard is active, or buffering it.

## 8. A concrete next iteration

If you only do one round of changes, I'd suggest:

- Make the results section auto-generated from a `--trials N` run with median/IQR.
- Add Rust (cpal) as a third column in the architecture diagram and table.
- Replace the `portaudio.dll` blob with a CMake `find_package(PortAudio)` and a vcpkg manifest.
- Add CI with the mock callback path so the suite is testable without an audio device.
- Move `stack.cpp` out of the repo root and tighten `.gitignore`.
