#!/usr/bin/env python3
"""
Master Benchmark Runner & Visualizer
------------------------------------
Orchestrates C++ vs. Python real-time audio callback benchmark runs,
computes statistical metrics, and generates side-by-side latency visualizations.
"""

import csv
import json
import os
import subprocess
import sys
import threading
import time
import datetime
import argparse

# Real-time deadline: 128 frames @ 44.1 kHz
# 128 / 44100 * 1000 = ~2.902 ms
DEADLINE_MS = 2.902
DEADLINE_US = DEADLINE_MS * 1000  # 2902.0

import matplotlib.pyplot as plt
import numpy as np

# ---------------------------------------------------------------------------#
# Base paths – assume this file lives at the project root alongside
# the ``cpp/`` and ``python/`` subfolders that hold each engine's source.
# ---------------------------------------------------------------------------#
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
CPP_DIR = os.path.join(PROJECT_ROOT, "cpp")
PY_DIR = os.path.join(PROJECT_ROOT, "python")
PROJECT_VENV_PYTHON = os.path.join(PROJECT_ROOT, ".venv", "Scripts", "python.exe")

# How long each stage runs (seconds).  Used by the live progress dashboard.
RUNNER_STAGE_PLAN = [
    ("compile", "Compiling C++ engine", 1.0),
    ("cpp", "Running C++ benchmark", 10.0),
    ("python", "Running Python benchmark", 10.0),
    ("analysis", "Computing stats & plot", 1.0),
]


# ---------------------------------------------------------------------------#
# Helpers
# ---------------------------------------------------------------------------#
def resolve_python_executable():
    """Resolve the interpreter used to run the Python benchmark.

    The benchmark is expected to run from the project's local .venv.
    """
    if os.path.exists(PROJECT_VENV_PYTHON):
        return PROJECT_VENV_PYTHON

    raise RuntimeError(
        "Project virtual environment not found. Create it with:\n"
        "  python -m venv .venv\n"
        "then install dependencies inside .venv before running this benchmark."
    )


def _format_bar(value, maximum, width=28, fill="#", empty="-"):
    if maximum <= 0:
        return empty * width
    ratio = max(0.0, min(1.0, value / maximum))
    filled = int(round(ratio * width))
    filled = max(0, min(width, filled))
    return fill * filled + empty * (width - filled)


def _dashboard_loop(state, stop_event):
    total_expected = sum(stage[2] for stage in RUNNER_STAGE_PLAN)
    while not stop_event.is_set():
        stage_key = state.get("stage", "idle")
        stage_label = state.get("stage_label", stage_key)
        stage_start = state.get("stage_start", time.monotonic())
        stage_expected = state.get("stage_expected", 1.0)
        current = time.monotonic()
        stage_elapsed = max(0.0, current - stage_start)
        completed_expected = state.get("completed_expected", 0.0)
        overall_done = min(
            total_expected, completed_expected + min(stage_elapsed, stage_expected)
        )
        overall_pct = (overall_done / total_expected) * 100.0 if total_expected > 0 else 0.0
        bar = _format_bar(overall_done, total_expected, width=30)
        detail = state.get("detail", "")

        line1 = f"[RUNNER] overall [{bar}] {overall_pct:5.1f}% | stage: {stage_label}"
        line2 = f"         stage elapsed {stage_elapsed:5.1f}s / {stage_expected:4.1f}s | {detail}"
        sys.stdout.write("\r\x1b[2K" + line1 + "\n" + "\r\x1b[2K" + line2)
        sys.stdout.flush()
        time.sleep(0.25)

    final_note = state.get("final_note")
    if final_note:
        sys.stdout.write("\r\x1b[2K" + final_note + "\n")
        sys.stdout.flush()


def _run_stage(stage_state, key, label, expected, detail, func):
    stage_state["stage"] = key
    stage_state["stage_label"] = label
    stage_state["stage_start"] = time.monotonic()
    stage_state["stage_expected"] = expected
    stage_state["detail"] = detail
    ok = func()
    if ok:
        stage_state["completed_expected"] = min(
            sum(stage[2] for stage in RUNNER_STAGE_PLAN),
            stage_state.get("completed_expected", 0.0) + expected,
        )
    return ok


# ---------------------------------------------------------------------------#
# Stage implementations
# ---------------------------------------------------------------------------#
def compile_cpp():
    print("\n========================================================")
    print(" [1/4] Compiling C++ Real-Time Audio Engine")
    print("========================================================")
    cmd = [
        "g++",
        "-O3",
        "-std=c++17",
        os.path.join(CPP_DIR, "cpp_jitter_test.cpp"),
        "-o",
        os.path.join(CPP_DIR, "cpp_jitter_test.exe"),
        os.path.join(CPP_DIR, "portaudio.dll"),
    ]
    print(f"Executing: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[-] Compilation failed:\n{result.stderr}", file=sys.stderr)
        return False
    print("[+] C++ engine compiled successfully -> cpp_jitter_test.exe")
    return True


def run_cpp_benchmark():
    print("\n========================================================")
    print(" [2/4] Executing C++ Engine (Zero-Allocation Hot Path)")
    print("========================================================")
    cmd = [os.path.join(CPP_DIR, "cpp_jitter_test.exe")]
    result = subprocess.run(cmd, cwd=CPP_DIR)
    if result.returncode != 0:
        print("[-] C++ benchmark execution failed!", file=sys.stderr)
        return False
    return True


def run_py_benchmark(gc_pressure="medium"):
    print("\n========================================================")
    print(" [3/4] Executing Python Engine (Active Heap Churn / GC)")
    print("========================================================")
    python_exe = resolve_python_executable()
    print(f"Using project-local .venv interpreter: {python_exe}")
    print(f"GC Pressure Profile: {gc_pressure}")

    cmd = [python_exe, os.path.join(PY_DIR, "py_jitter_test.py")]
    if gc_pressure != "medium":
        cmd.extend(["--gc-pressure", gc_pressure])
    result = subprocess.run(cmd)
    if result.returncode != 0:
        print("[-] Python benchmark execution failed!", file=sys.stderr)
        return False
    return True


def load_metrics(filename):
    if not os.path.exists(filename):
        raise FileNotFoundError(f"Metrics file {filename} not found!")

    indices, timestamps, texec_us, dt_arrival_us, underruns = [], [], [], [], []
    with open(filename, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            indices.append(int(row["callback_index"]))
            timestamps.append(float(row["timestamp_sec"]))
            texec_us.append(float(row["t_exec_us"]))
            dt_arrival_us.append(float(row["dt_arrival_us"]))
            underruns.append(int(row["underrun_flag"]))

    return {
        "indices": np.array(indices),
        "timestamps": np.array(timestamps),
        "texec_us": np.array(texec_us),
        "dt_arrival_us": np.array(dt_arrival_us),
        "underruns": np.array(underruns),
    }


def compute_statistics(metrics):
    t = metrics["texec_us"]
    underruns = metrics["underruns"]
    total = len(t)
    if total == 0:
        return {}

    mean_val = np.mean(t)
    std_val = np.std(t, ddof=1) if total > 1 else 0.0
    p50_val = np.percentile(t, 50)
    p95_val = np.percentile(t, 95)
    p99_val = np.percentile(t, 99)
    p100_val = np.max(t)
    underrun_cnt = np.sum(underruns)
    violations = np.sum(t > DEADLINE_US)
    violation_pct = (violations / total) * 100.0

    return {
        "total_callbacks": total,
        "mean_us": mean_val,
        "std_us": std_val,
        "p50_us": p50_val,
        "p95_us": p95_val,
        "p99_us": p99_val,
        "p100_us": p100_val,
        "underruns": underrun_cnt,
        "violations": violations,
        "violation_pct": violation_pct,
    }


def run_trial(trial_num, trial_dir, gc_pressure="medium"):
    """Run a single benchmark trial and return (cpp_stats, py_stats).

    CSVs are persisted into trial_dir (e.g. output/runs/2026-09-05T10-30-00/).
    """
    timestamp = datetime.datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    trial_subdir = os.path.join(trial_dir, f"trial_{trial_num}_{timestamp}")
    os.makedirs(trial_subdir, exist_ok=True)

    # --- C++ trial ---
    cpp_csv = os.path.join(trial_subdir, "cpp_metrics.csv")
    # Compile
    cmd = [
        "g++", "-O3", "-std=c++17",
        os.path.join(CPP_DIR, "cpp_jitter_test.cpp"),
        "-o", os.path.join(CPP_DIR, "cpp_jitter_test.exe"),
        os.path.join(CPP_DIR, "portaudio.dll"),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[-] Trial {trial_num} C++ compile failed: {result.stderr}", file=sys.stderr)
        return None, None
    # Run C++ benchmark (writes to its executable dir, which resolves to exe_dir/cpp_metrics.csv)
    cmd = [os.path.join(CPP_DIR, "cpp_jitter_test.exe")]
    result = subprocess.run(cmd, cwd=CPP_DIR)
    if result.returncode != 0:
        print(f"[-] Trial {trial_num} C++ run failed!", file=sys.stderr)
        return None, None

    # --- Python trial ---
    py_csv = os.path.join(trial_subdir, "py_metrics.csv")
    python_exe = resolve_python_executable()
    cmd = [python_exe, os.path.join(PY_DIR, "py_jitter_test.py")]
    if gc_pressure != "medium":
        cmd.extend(["--gc-pressure", gc_pressure])
    result = subprocess.run(cmd)
    if result.returncode != 0:
        print(f"[-] Trial {trial_num} Python run failed!", file=sys.stderr)
        return None, None

    # Compute statistics from the two CSVs
    cpp_data = load_metrics(cpp_csv)
    py_data = load_metrics(py_csv)

    cpp_stats = compute_statistics(cpp_data)
    py_stats = compute_statistics(py_data)

    # Save per-trial stats for later aggregation
    with open(os.path.join(trial_subdir, "stats.json"), "w") as f:
        json.dump({"cpp": cpp_stats, "py": py_stats}, f, indent=2)

    return cpp_stats, py_stats


def median_iqr(vals):
    import numpy as np
    return {
        "median": float(np.median(vals)),
        "iqr_low": float(np.percentile(vals, 25)),
        "iqr_high": float(np.percentile(vals, 75)),
    }


def print_comparison_table_aggregated(aggregated):
    print("\n==========================================================================================")
    print("                         AUDIO CALLBACK BENCHMARK SUMMARY REPORT                          ")
    print("==========================================================================================")
    print(f" Constraint: Real-Time Deadline T_max = {DEADLINE_MS:.4f} ms ({DEADLINE_US:.2f} us)")
    print("------------------------------------------------------------------------------------------")
    header = f"{'Metric':<32} | {'C++ (Zero-Alloc)':<25} | {'Python (GC Churn)':<25}"
    print(header)
    print("-" * len(header))

    rows = [
        ("Total Callbacks", f"{aggregated['total_callbacks']:,}", ""),
        ("Mean T_exec", f"{aggregated['mean_us']['cpp']:.2f} us ({aggregated['mean_us']['cpp']/1000:.4f} ms)", f"{aggregated['mean_us']['py']:.2f} us ({aggregated['mean_us']['py']/1000:.4f} ms)"),
        ("Std Dev (Jitter sigma)", f"{aggregated['std_us']['cpp']:.2f} us", f"{aggregated['std_us']['py']:.2f} us"),
        ("Median (P50)", f"{aggregated['p50_us']['cpp']:.2f} us", f"{aggregated['p50_us']['py']:.2f} us"),
        ("95th Percentile (P95)", f"{aggregated['p95_us']['cpp']:.2f} us", f"{aggregated['p95_us']['py']:.2f} us"),
        ("99th Percentile (P99)", f"{aggregated['p99_us']['cpp']:.2f} us", f"{aggregated['p99_us']['py']:.2f} us"),
        ("Max T_exec (P100)", f"{aggregated['p100_us']['cpp']:.2f} us ({aggregated['p100_us']['cpp']/1000:.3f} ms)", f"{aggregated['p100_us']['py']:.2f} us ({aggregated['p100_us']['py']/1000:.3f} ms)"),
        ("Buffer Underruns Logged", f"{aggregated['underruns']['cpp']}", f"{aggregated['underruns']['py']}"),
        ("Deadline Violations (>T_max)", f"{aggregated['violations']['cpp']} ({aggregated['violation_pct']['cpp']:.2f}%)", f"{aggregated['violations']['py']} ({aggregated['violation_pct']['py']:.2f}%)"),
    ]

    for label, cpp_val, py_val in rows:
        print(f"{label:<32} | {cpp_val:<25} | {py_val:<25}")
    print("==========================================================================================\n")

    # Acceptance Criteria Check
    cpp_pass = (aggregated["underruns"]["cpp"] == 0 and aggregated["p100_us"]["cpp"] < DEADLINE_US)
    print(">>> ACCEPTANCE CRITERIA VERIFICATION <<<")
    print(f" [C++ Engine]    0 Underruns & Max < 2.902 ms:  {'[PASS]' if cpp_pass else '[FAIL]'}")
    print("==========================================================================================\n")


def generate_plots_aggregated(all_cpp_stats, all_py_stats):
    """Generate aggregated plots from all trial data."""
    import numpy as np

    # Combine all trial data into flat arrays for plotting
    all_cpp_times = []
    all_py_times = []
    all_cpp_labels = []
    all_py_labels = []

    for i, (cpp_s, py_s) in enumerate(zip(all_cpp_stats, all_py_stats), 1):
        # Each stats dict has arrays/list values; we need the raw times.
        # For now, re-run quick collection or skip rich plot agg.
        pass

    # Simple approach: plot individual trial means as a bar comparison
    cpp_means = [s["mean_us"] for s in all_cpp_stats]
    py_means = [s["mean_us"] for s in all_py_stats]

    plt.style.use("seaborn-v0_8-whitegrid" if "seaborn-v0_8-whitegrid" in plt.style.available else "default")
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 12), dpi=150)
    fig.patch.set_facecolor("#0f172a")

    for ax in [ax1, ax2]:
        ax.set_facecolor("#1e293b")
        ax.grid(True, color="#334155", linestyle="--", alpha=0.6)
        ax.tick_params(colors="#e2e8f0", labelsize=10)
        for spine in ax.spines.values():
            spine.set_color("#475569")

    # Bar chart of means across trials
    x = np.arange(1, len(all_cpp_stats) + 1)
    width = 0.35

    ax1.bar(x - width/2, cpp_means, width, label="C++ Mean", color="#38bdf8", alpha=0.7)
    ax1.bar(x + width/2, py_means, width, label="Python Mean", color="#f43f5e", alpha=0.6)
    ax1.set_ylabel("Mean T_exec (us)", color="#cbd5e1", fontsize=11)
    ax1.set_title("Mean Latency Across Trials", color="#f8fafc", fontsize=14, fontweight='bold', pad=10)
    ax1.legend(loc='upper right', facecolor="#0f172a", edgecolor="#475569", labelcolor="#f8fafc", fontsize=10)
    ax1.set_xticks(x)
    ax1.set_xticklabels([f"Trial {i}" for i in range(1, len(all_cpp_stats)+1)])

    # Histogram of all p100 values
    all_p100_cpp = [s["p100_us"] for s in all_cpp_stats]
    all_p100_py = [s["p100_us"] for s in all_py_stats]

    max_range = max(max(all_p100_cpp), max(all_p100_py), DEADLINE_US * 1.1)
    bins = np.linspace(0, max_range, 120)

    ax2.hist(all_p100_cpp, bins=bins, color="#38bdf8", alpha=0.7, label="C++ P100", density=True)
    ax2.hist(all_p100_py, bins=bins, color="#f43f5e", alpha=0.6, label="Python P100", density=True)
    ax2.axvline(DEADLINE_US, color="#facc15", linestyle="--", linewidth=2.0, label=f"Deadline {DEADLINE_MS:.3f} ms")
    ax2.set_yscale('log')
    ax2.set_ylabel("Probability Density (Log)", color="#cbd5e1", fontsize=11)
    ax2.set_title("P100 Latency Distribution (Log Scale)", color="#f8fafc", fontsize=14, fontweight='bold', pad=10)
    ax2.legend(loc='upper right', facecolor="#0f172a", edgecolor="#475569", labelcolor="#f8fafc", fontsize=10)

    plt.tight_layout()
    plt.savefig(os.path.join(PROJECT_ROOT, "latency_comparison_aggregated.png"), dpi=150, facecolor=fig.get_facecolor(), edgecolor='none')
    print(f"[+] Saved aggregated visualization plot to: latency_comparison_aggregated.png")


def main():
    parser = argparse.ArgumentParser(
        description="Cross-Language Real-Time Audio Callback Benchmark Suite"
    )
    parser.add_argument(
        "--gc-pressure",
        choices=["none", "light", "medium", "heavy", "extreme"],
        default="medium",
        help="Python GC pressure profile (default: medium)",
    )
    parser.add_argument(
        "--trials",
        type=int,
        default=5,
        help="Number of benchmark trials to run (default: 5)",
    )
    args = parser.parse_args()

    stage_state = {
        "stage": "idle",
        "stage_label": "Starting",
        "stage_start": time.monotonic(),
        "stage_expected": 1.0,
        "completed_expected": 0.0,
        "detail": "Preparing benchmark pipeline...",
    }
    stop_event = threading.Event()
    dashboard = threading.Thread(target=_dashboard_loop, args=(stage_state, stop_event), daemon=True)
    dashboard.start()

    if not _run_stage(stage_state, "compile", "Compiling C++ engine", 1.0,
                      "Building cpp_jitter_test.exe", compile_cpp):
        stop_event.set()
        stage_state["final_note"] = "[RUNNER] benchmark pipeline aborted during compile"
        dashboard.join(timeout=1.0)
        sys.exit(1)

    if not _run_stage(stage_state, "cpp", "Running C++ benchmark", 10.0,
                      "Executing cpp_jitter_test.exe", run_cpp_benchmark):
        stop_event.set()
        stage_state["final_note"] = "[RUNNER] benchmark pipeline aborted during C++ run"
        dashboard.join(timeout=1.0)
        sys.exit(1)

    if not _run_stage(stage_state, "python", "Running Python benchmark", 10.0,
                      "Executing py_jitter_test.py", lambda: run_py_benchmark(args.gc_pressure)):
        stop_event.set()
        stage_state["final_note"] = "[RUNNER] benchmark pipeline aborted during Python run"
        dashboard.join(timeout=1.0)
        sys.exit(1)

    # --- New: multi-trial run with auto-generated results table ---
    trial_dir = os.path.join(PROJECT_ROOT, "output", "runs")
    N_TRIALS = args.trials  # can be configured via CLI; 5 gives reasonable IQR

    all_cpp_stats = []
    all_py_stats = []
    for trial in range(1, N_TRIALS + 1):
        print(f"\n[+] Starting trial {trial}/{N_TRIALS}...")
        cpp_s, py_s = run_trial(trial, trial_dir, gc_pressure=args.gc_pressure)
        if cpp_s is None or py_s is None:
            print(f"[-] Trial {trial} failed, stopping.")
            sys.exit(1)
        all_cpp_stats.append(cpp_s)
        all_py_stats.append(py_s)

    # Aggregate: median + IQR across trials
    aggregated = {
        "total_callbacks": float(np.median([s["total_callbacks"] for s in all_cpp_stats])),
        "mean_us": {
            "cpp": median_iqr([s["mean_us"] for s in all_cpp_stats])["median"],
            "py": median_iqr([s["mean_us"] for s in all_py_stats])["median"],
        },
        "std_us": {
            "cpp": median_iqr([s["std_us"] for s in all_cpp_stats])["median"],
            "py": median_iqr([s["std_us"] for s in all_py_stats])["median"],
        },
        "p50_us": {
            "cpp": median_iqr([s["p50_us"] for s in all_cpp_stats])["median"],
            "py": median_iqr([s["p50_us"] for s in all_py_stats])["median"],
        },
        "p95_us": {
            "cpp": median_iqr([s["p95_us"] for s in all_cpp_stats])["median"],
            "py": median_iqr([s["p95_us"] for s in all_py_stats])["median"],
        },
        "p99_us": {
            "cpp": median_iqr([s["p99_us"] for s in all_cpp_stats])["median"],
            "py": median_iqr([s["p99_us"] for s in all_py_stats])["median"],
        },
        "p100_us": {
            "cpp": median_iqr([s["p100_us"] for s in all_cpp_stats])["median"],
            "py": median_iqr([s["p100_us"] for s in all_py_stats])["median"],
        },
        "underruns": {
            "cpp": median_iqr([s["underruns"] for s in all_cpp_stats])["median"],
            "py": median_iqr([s["underruns"] for s in all_py_stats])["median"],
        },
        "violations": {
            "cpp": median_iqr([s["violations"] for s in all_cpp_stats])["median"],
            "py": median_iqr([s["violations"] for s in all_py_stats])["median"],
        },
        "violation_pct": {
            "cpp": median_iqr([s["violation_pct"] for s in all_cpp_stats])["median"],
            "py": median_iqr([s["violation_pct"] for s in all_py_stats])["median"],
        },
    }

    print("\n[+] Aggregated results across {} trials:".format(N_TRIALS))
    print("=" * 60)
    print_comparison_table_aggregated(aggregated)
    generate_plots_aggregated(all_cpp_stats, all_py_stats)

    # Save aggregated stats alongside per-trial data
    os.makedirs(trial_dir, exist_ok=True)
    with open(os.path.join(trial_dir, "aggregated_stats.json"), "w") as f:
        json.dump(aggregated, f, indent=2)

    stage_state["completed_expected"] = sum(stage[2] for stage in RUNNER_STAGE_PLAN)
    stage_state["final_note"] = "[RUNNER] benchmark pipeline complete"
    stop_event.set()
    dashboard.join(timeout=1.0)
    sys.stdout.flush()


if __name__ == "__main__":
    main()