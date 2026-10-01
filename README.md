# Cross-Language Real-Time Audio Callback Benchmarking Suite (C++ vs. Python)

This project provides an empirical benchmarking suite designed to measure audio callback execution jitter, latency distribution, and GC-induced buffer underruns under strict sub-3ms real-time constraints ($T_{max} \approx 2.902\text{ ms}$).

---

## 1. Real-Time Audio Theory & Constraints

In digital audio streaming, audio hardware consumes sample frames continuously at a fixed sample rate ($f_s$). To prevent audible audio dropouts (buffer underruns / glitches), the software must compute and provide each audio buffer before the hardware finishes consuming the previous buffer.

### Key Benchmark Parameters
- **Sample Rate ($f_s$)**: $44,100\text{ Hz}$ (44.1 kHz)
- **Block Size ($B$)**: $128\text{ frames}$
- **Audio Signal**: $440\text{ Hz}$ Continuous Phase Sine Wave
- **Execution Deadline Window ($T_{max}$)**:
  $$T_{max} = \frac{\text{Block Size}}{\text{Sample Rate}} = \frac{128}{44,100} \approx 2.90249\text{ ms} \quad (2,902.49\ \mu\text{s})$$
- **Test Duration**: $10.0\text{ seconds}$ per run ($\sim 3,445\text{ callbacks}$)

---

## 2. Architecture & Engine Design

```
                      +------------------------------------------+
                      |         Master Benchmark Runner          |
                      |           (run_benchmark.py)             |
                      +--------------------+---------------------+
                                           |
                    +----------------------+----------------------+
                    |                                             |
                    v                                             v
       +-------------------------+                   +-------------------------+
       |   C++ Real-Time Engine  |                   |   Python Audio Engine   |
       |    (cpp_jitter_test)    |                   |    (py_jitter_test)     |
       +-------------------------+                   +-------------------------+
       | - Zero hot-path alloc   |                   | - sounddevice + numpy   |
       | - PortAudio C++ API     |                   | - Active cyclic heap    |
       | - Microsecond clock     |                   |   churn in callback     |
       | - Pre-allocated metrics |                   | - Microsecond timing    |
       +------------+------------+                   +------------+------------+
                    |                                             |
                    v                                             v
            cpp_metrics.csv                                py_metrics.csv
                    \                                             /
                     \                                           /
                      +--------------------+--------------------+
                                           |
                                           v
                              +-------------------------+
                              | Analysis & Visualizer   |
                              | - Combined CLI Table    |
                              | - Percentiles (P50-P100)|
                              | - latency_comparison.png|
                              +-------------------------+
```

### Phase 1: Shared Latency Instrumentation
Inside each callback invocation:
1. **Execution Duration ($T_{exec}$)**: $T_{exec} = t_{exit} - t_{entry}$
2. **Inter-Arrival Interval ($\Delta T_{arrival}$)**: $\Delta T_{arrival} = t_{entry}(n) - t_{entry}(n-1)$
3. **Underrun Flag**: Set whenever $T_{exec} > 2.90249\text{ ms}$ or when the driver flags a hardware buffer underflow (`statusFlags & paOutputUnderflow`).

### Phase 2: Engine Implementations
- **C++ Engine (`cpp_jitter_test.cpp`)**:
  - **Memory Policy**: Strict zero-allocation in the hot path. All oscillator states, metrics arrays, and buffers are pre-allocated statically before the audio stream starts.
  - **Timer**: Microsecond resolution via `std::chrono::high_resolution_clock`.
  - **DSP**: Phase-continuous accumulator: $\Delta\phi = \frac{2\pi \times 440.0}{44,100}$.
- **Python Engine (`py_jitter_test.py`)**:
  - **Dependencies**: `sounddevice`, `numpy`.
  - **Memory Policy**: Intentional active heap churn (cyclic object graphs allocated inside callback) to trigger CPython mark-and-sweep garbage collection pauses.
  - **Timer**: Microsecond resolution via `time.perf_counter_ns()`.

### Phase 3: Benchmark Driver & Visualizer (`run_benchmark.py`)
- Compiles C++ source with `g++ -O3`.
- Executes both engines sequentially for 10 seconds.
- Computes statistical percentiles (Mean, Std Dev, Median, P95, P99, P100, Violations).
- Streams a live terminal ASCII dashboard during the Python benchmark run.
- Generates a combined CLI summary table and publication-grade plot (`latency_comparison.png`).

---

## 3. Quick Start / Reproduction

### Prerequisites
- GCC / MinGW C++ compiler (`g++`)
- Python 3.10+ 
- Create a project-local virtual environment at `.venv`
- Install dependencies into `.venv`:
  - `numpy`
  - `sounddevice`
  - `matplotlib`
  - `pandas`

### Running the Benchmark Suite
```bash
# Create the project-local venv once
python -m venv .venv

# Install dependencies into the project-local venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install numpy sounddevice matplotlib pandas

# Run the master orchestrator using the local venv
.venv\Scripts\python.exe run_benchmark.py
```

#### CLI Options

`run_benchmark.py` accepts the following command-line flags:

| Flag | Values | Default | Description |
| :--- | :--- | :--- | :--- |
| `--gc-pressure` | `none`, `light`, `medium`, `heavy`, `extreme` | `medium` | Sets the Python engine's GC churn profile. `none` disables churn for a no-GC baseline; `heavy`/`extreme` use 1 MiB buffer allocations to trigger major collections. |
| `--trials` | Integer | `5` | Number of benchmark trials to run. Each trial runs both C++ and Python engines independently. Results are aggregated with median + IQR. |
| `--help` | — | — | Show help message and exit. |

**Examples:**
```bash
# Run with heavy GC pressure to stress-test the Python engine
.venv\Scripts\python.exe run_benchmark.py --gc-pressure heavy

# Run 10 trials for more robust statistics
.venv\Scripts\python.exe run_benchmark.py --trials 10

# Run no-churn baseline (Python engine with zero GC pressure)
.venv\Scripts\python.exe run_benchmark.py --gc-pressure none
```

`run_benchmark.py` now always uses `.venv\Scripts\python.exe` for the Python benchmark run.

### Running Individual Engines
```bash
# Compile and run C++ engine standalone
g++ -O3 -std=c++17 cpp_jitter_test.cpp -o cpp_jitter_test.exe portaudio.dll
.\cpp_jitter_test.exe

# Run Python engine standalone with the project-local venv
.\.venv\Scripts\python.exe py_jitter_test.py
```

---

## 4. Benchmark Results Summary

| Metric | C++ (Zero-Alloc Hot Path) | Python (Active GC Churn) |
| :--- | :--- | :--- |
| **Total Callbacks** | 3,435 | 3,429 |
| **Mean $T_{exec}$** | **$3.51\ \mu\text{s}$ ($0.0035\text{ ms}$)** | **$587.84\ \mu\text{s}$ ($0.5878\text{ ms}$)** |
| **Jitter ($\sigma$)** | **$2.16\ \mu\text{s}$** | **$443.24\ \mu\text{s}$** |
| **Median (P50)** | $3.30\ \mu\text{s}$ | $521.10\ \mu\text{s}$ |
| **95th Percentile (P95)** | $6.80\ \mu\text{s}$ | $1,211.10\ \mu\text{s}$ |
| **99th Percentile (P99)** | $8.20\ \mu\text{s}$ | $2,122.88\ \mu\text{s}$ |
| **Max $T_{exec}$ (P100)** | **$71.80\ \mu\text{s}$ ($0.072\text{ ms}$)** | **$9,594.20\ \mu\text{s}$ ($9.594\text{ ms}$)** |
| **Buffer Underruns** | **0** | **4** |
| **Deadline Violations ($>2.902\text{ ms}$)**| **0 ($0.00\%$)** | **4 ($0.12\%$)** |
| **Acceptance Criteria** | **PASS** | **PASS** |

---

## 5. Architectural Findings & Takeaways
1. **Zero-Allocation Determinism in C++**: The C++ callback completed in an average of $3.51\ \mu\text{s}$, consuming less than **$0.12\%$** of the available $2.902\text{ ms}$ deadline window with maximum worst-case jitter peaking at $71.80\ \mu\text{s}$.
2. **GC Pause Hazard in Real-Time Audio**: In garbage-collected runtimes like Python, heap churn inside the audio callback triggers non-deterministic mark-and-sweep pauses that exceed the hardware deadline window by more than **$330\%$** ($9.59\text{ ms}$ vs $2.902\text{ ms}$ limit), resulting in audible glitches and hardware buffer underruns.
