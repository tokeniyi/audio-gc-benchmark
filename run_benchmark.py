#!/usr/bin/env python3
"""
Master Benchmark Runner & Visualizer
------------------------------------
Orchestrates C++ vs. Python real-time audio callback benchmark runs,
computes statistical metrics, and generates side-by-side latency visualizations.
"""

import csv
import os
import subprocess
import sys

import matplotlib.pyplot as plt
import numpy as np

DEADLINE_MS = 128.0 / 44.1  # ~2.90249 ms
DEADLINE_US = DEADLINE_MS * 1000.0
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
PROJECT_VENV_PYTHON = os.path.join(PROJECT_ROOT, ".venv", "Scripts", "python.exe")


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

def compile_cpp():
    print("\n========================================================")
    print(" [1/4] Compiling C++ Real-Time Audio Engine")
    print("========================================================")
    cmd = ["g++", "-O3", "-std=c++17", "cpp_jitter_test.cpp", "-o", "cpp_jitter_test.exe", "portaudio.dll"]
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
    cmd = [".\\cpp_jitter_test.exe"]
    result = subprocess.run(cmd)
    if result.returncode != 0:
        print("[-] C++ benchmark execution failed!", file=sys.stderr)
        return False
    return True

def run_py_benchmark():
    print("\n========================================================")
    print(" [3/4] Executing Python Engine (Active Heap Churn / GC)")
    print("========================================================")
    python_exe = resolve_python_executable()
    print(f"Using project-local .venv interpreter: {python_exe}")

    cmd = [python_exe, "py_jitter_test.py"]
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

def print_comparison_table(cpp_stats, py_stats):
    print("\n==========================================================================================")
    print("                         AUDIO CALLBACK BENCHMARK SUMMARY REPORT                          ")
    print("==========================================================================================")
    print(f" Constraint: Real-Time Deadline T_max = {DEADLINE_MS:.4f} ms ({DEADLINE_US:.2f} us)")
    print("------------------------------------------------------------------------------------------")
    header = f"{'Metric':<32} | {'C++ (Zero-Alloc)':<25} | {'Python (GC Churn)':<25}"
    print(header)
    print("-" * len(header))
    
    rows = [
        ("Total Callbacks", f"{cpp_stats['total_callbacks']:,}", f"{py_stats['total_callbacks']:,}"),
        ("Mean T_exec", f"{cpp_stats['mean_us']:.2f} us ({cpp_stats['mean_us']/1000:.4f} ms)", f"{py_stats['mean_us']:.2f} us ({py_stats['mean_us']/1000:.4f} ms)"),
        ("Std Dev (Jitter sigma)", f"{cpp_stats['std_us']:.2f} us", f"{py_stats['std_us']:.2f} us"),
        ("Median (P50)", f"{cpp_stats['p50_us']:.2f} us", f"{py_stats['p50_us']:.2f} us"),
        ("95th Percentile (P95)", f"{cpp_stats['p95_us']:.2f} us", f"{py_stats['p95_us']:.2f} us"),
        ("99th Percentile (P99)", f"{cpp_stats['p99_us']:.2f} us", f"{py_stats['p99_us']:.2f} us"),
        ("Max T_exec (P100)", f"{cpp_stats['p100_us']:.2f} us ({cpp_stats['p100_us']/1000:.3f} ms)", f"{py_stats['p100_us']:.2f} us ({py_stats['p100_us']/1000:.3f} ms)"),
        ("Buffer Underruns Logged", f"{cpp_stats['underruns']}", f"{py_stats['underruns']}"),
        ("Deadline Violations (>T_max)", f"{cpp_stats['violations']} ({cpp_stats['violation_pct']:.2f}%)", f"{py_stats['violations']} ({py_stats['violation_pct']:.2f}%)"),
    ]
    
    for label, cpp_val, py_val in rows:
        print(f"{label:<32} | {cpp_val:<25} | {py_val:<25}")
    print("==========================================================================================\n")

    # Acceptance Criteria Check
    cpp_pass = (cpp_stats["underruns"] == 0 and cpp_stats["p100_us"] < DEADLINE_US)
    py_spikes = (py_stats["p100_us"] > DEADLINE_US or py_stats["violations"] > 0 or py_stats["underruns"] > 0)

    print(">>> ACCEPTANCE CRITERIA VERIFICATION <<<")
    print(f" [C++ Engine]    0 Underruns & Max < 2.902 ms:  {'[PASS]' if cpp_pass else '[FAIL]'}")
    print(f" [Python Engine] GC Latency Spikes > 2.902 ms:  {'[PASS]' if py_spikes else '[FAIL]'}")
    print("==========================================================================================\n")

def generate_plots(cpp_metrics, py_metrics, output_file="latency_comparison.png"):
    print("\n========================================================")
    print(" [4/4] Generating Latency Comparison Visualization")
    print("========================================================")
    
    plt.style.use("seaborn-v0_8-whitegrid" if "seaborn-v0_8-whitegrid" in plt.style.available else "default")
    fig, axes = plt.subplots(3, 1, figsize=(14, 12), dpi=150)
    fig.patch.set_facecolor('#0f172a')
    
    for ax in axes:
        ax.set_facecolor('#1e293b')
        ax.grid(True, color='#334155', linestyle='--', alpha=0.6)
        ax.tick_params(colors='#e2e8f0', labelsize=10)
        for spine in ax.spines.values():
            spine.set_color('#475569')

    # Subplot 1: Time Series Latency vs Elapsed Time
    ax1 = axes[0]
    ax1.plot(cpp_metrics["timestamps"], cpp_metrics["texec_us"] / 1000.0, 
             color='#38bdf8', alpha=0.85, linewidth=1.2, label="C++ (Zero-Alloc Real-Time PortAudio)")
    ax1.plot(py_metrics["timestamps"], py_metrics["texec_us"] / 1000.0, 
             color='#f43f5e', alpha=0.8, linewidth=1.0, label="Python (Active GC Heap Churn)")
    
    # Real-Time Deadline Threshold
    ax1.axhline(DEADLINE_MS, color='#facc15', linestyle='--', linewidth=2.0, 
                label=f"Real-Time Deadline $T_{{max}} = {DEADLINE_MS:.3f}$ ms (128 frames @ 44.1 kHz)")
    
    ax1.set_title("Audio Callback Execution Time ($T_{exec}$) Over Time", color='#f8fafc', fontsize=14, fontweight='bold', pad=10)
    ax1.set_xlabel("Elapsed Time (seconds)", color='#cbd5e1', fontsize=11)
    ax1.set_ylabel("Execution Time $T_{exec}$ (ms)", color='#cbd5e1', fontsize=11)
    ax1.legend(loc='upper right', facecolor='#0f172a', edgecolor='#475569', labelcolor='#f8fafc', fontsize=10)

    # Subplot 2: Histogram / Distribution
    ax2 = axes[1]
    cpp_ms = cpp_metrics["texec_us"] / 1000.0
    py_ms = py_metrics["texec_us"] / 1000.0
    
    max_range = max(np.max(cpp_ms), np.max(py_ms), DEADLINE_MS * 1.1)
    bins = np.linspace(0, max_range, 120)
    ax2.hist(cpp_ms, bins=bins, color='#38bdf8', alpha=0.7, label="C++ Latency Distribution", density=True)
    ax2.hist(py_ms, bins=bins, color='#f43f5e', alpha=0.6, label="Python Latency Distribution (GC Spikes)", density=True)
    ax2.axvline(DEADLINE_MS, color='#facc15', linestyle='--', linewidth=2.0, label=f"Deadline $T_{{max}} = {DEADLINE_MS:.3f}$ ms")
    
    ax2.set_yscale('log')
    ax2.set_title("Execution Time Distribution & Tail Latencies (Log Scale)", color='#f8fafc', fontsize=14, fontweight='bold', pad=10)
    ax2.set_xlabel("Execution Time $T_{exec}$ (ms)", color='#cbd5e1', fontsize=11)
    ax2.set_ylabel("Probability Density (Log)", color='#cbd5e1', fontsize=11)
    ax2.legend(loc='upper right', facecolor='#0f172a', edgecolor='#475569', labelcolor='#f8fafc', fontsize=10)

    # Subplot 3: Inter-Arrival Delta T_arrival
    ax3 = axes[2]
    ax3.plot(cpp_metrics["timestamps"][1:], cpp_metrics["dt_arrival_us"][1:] / 1000.0,
             color='#34d399', alpha=0.75, linewidth=1.0, label=r"C++ Callback Inter-Arrival Interval ($\Delta T_{arrival}$)")
    ax3.plot(py_metrics["timestamps"][1:], py_metrics["dt_arrival_us"][1:] / 1000.0,
             color='#fb923c', alpha=0.65, linewidth=1.0, label=r"Python Callback Inter-Arrival Interval ($\Delta T_{arrival}$)")
    ax3.axhline(DEADLINE_MS, color='#facc15', linestyle=':', linewidth=1.5, label=f"Expected Period ($128/44100 \\approx {DEADLINE_MS:.3f}$ ms)")
    
    ax3.set_title(r"Callback Inter-Arrival Jitter ($\Delta T_{arrival}$)", color='#f8fafc', fontsize=14, fontweight='bold', pad=10)
    ax3.set_xlabel("Elapsed Time (seconds)", color='#cbd5e1', fontsize=11)
    ax3.set_ylabel(r"Arrival Delta $\Delta T$ (ms)", color='#cbd5e1', fontsize=11)
    ax3.legend(loc='upper right', facecolor='#0f172a', edgecolor='#475569', labelcolor='#f8fafc', fontsize=10)

    plt.tight_layout()
    plt.savefig(output_file, dpi=150, facecolor=fig.get_facecolor(), edgecolor='none')
    print(f"[+] Saved visualization plot to: {output_file}")

def main():
    if not compile_cpp():
        sys.exit(1)
        
    if not run_cpp_benchmark():
        sys.exit(1)
        
    if not run_py_benchmark():
        sys.exit(1)
        
    print("\n[+] Processing benchmark datasets...")
    cpp_data = load_metrics("cpp_metrics.csv")
    py_data = load_metrics("py_metrics.csv")
    
    cpp_stats = compute_statistics(cpp_data)
    py_stats = compute_statistics(py_data)
    
    print_comparison_table(cpp_stats, py_stats)
    generate_plots(cpp_data, py_data, "latency_comparison.png")

if __name__ == "__main__":
    main()
