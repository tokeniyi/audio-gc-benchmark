#!/usr/bin/env python3
"""
Real-Time Audio Callback Jitter & GC Benchmark (Python Engine)
--------------------------------------------------------------
Measures audio callback execution jitter, latency distribution, and GC-induced
buffer underruns under sub-3ms real-time constraints (T_max ≈ 2.902 ms).
"""

import time
import csv
import sys
import gc
import numpy as np
import sounddevice as sd

# Benchmark Constraints & Constants
SAMPLE_RATE = 44100
BLOCK_SIZE = 128
SINE_FREQ = 440.0
TWO_PI = 2.0 * np.pi
DEADLINE_MS = (BLOCK_SIZE / SAMPLE_RATE) * 1000.0   # ~2.90249 ms
DEADLINE_US = DEADLINE_MS * 1000.0                  # ~2902.49 us
RUN_DURATION_SEC = 10.0
MAX_RECORDS = 10000


def _format_bar(value, maximum, width=24, fill="#", empty="-"):
    if maximum <= 0:
        return empty * width
    ratio = max(0.0, min(1.0, value / maximum))
    filled = int(round(ratio * width))
    filled = max(0, min(width, filled))
    return fill * filled + empty * (width - filled)


def _format_tail(values, width=32):
    if not values:
        return ""
    blocks = " .:-=+*#%@"
    tail = values[-width:]
    lo = min(tail)
    hi = max(tail)
    if hi <= lo:
        return blocks[-1] * len(tail)
    out = []
    for value in tail:
        ratio = (value - lo) / (hi - lo)
        idx = int(round(ratio * (len(blocks) - 1)))
        idx = max(0, min(len(blocks) - 1, idx))
        out.append(blocks[idx])
    return "".join(out)

class PythonJitterBenchmark:
    def __init__(self):
        self.phase = 0.0
        self.phase_increment = (TWO_PI * SINE_FREQ) / SAMPLE_RATE
        self.amplitude = 0.25
        
        # Pre-allocate metric storage
        self.records = []
        self.benchmark_start_ns = None
        self.prev_entry_ns = None
        self.first_callback = True
        self.callback_count = 0
        self.underrun_count = 0

    def audio_callback(self, outdata, frames, time_info, status):
        # 1. Entry Timestamp
        t_entry = time.perf_counter_ns()

        if self.benchmark_start_ns is None:
            self.benchmark_start_ns = t_entry

        # Inter-arrival Delta T
        if not self.first_callback:
            dt_arrival_us = (t_entry - self.prev_entry_ns) / 1000.0
        else:
            dt_arrival_us = 0.0
            self.first_callback = False
        self.prev_entry_ns = t_entry

        # 2. Phase-continuous Sine Wave DSP Generation
        t = np.arange(frames)
        phases = self.phase + t * self.phase_increment
        outdata[:, 0] = self.amplitude * np.sin(phases)
        self.phase = (self.phase + frames * self.phase_increment) % TWO_PI

        # 3. Active Heap Churn (Intentional cyclic memory churn to trigger Python GC mark-and-sweep)
        # Creating cyclic reference graphs forces full mark-and-sweep collections
        churn_graph = []
        for _ in range(120):
            node_a = {"data": np.zeros(250, dtype=np.float32), "ref": None}
            node_b = {"data": np.zeros(250, dtype=np.float32), "ref": node_a}
            node_a["ref"] = node_b
            churn_graph.append(node_a)
        del churn_graph

        # 4. Exit Timestamp & Metrics Calculation
        t_exit = time.perf_counter_ns()
        t_exec_us = (t_exit - t_entry) / 1000.0
        elapsed_sec = (t_exit - self.benchmark_start_ns) / 1e9

        # Underrun Detection: Driver status flag OR deadline overrun (>2.902 ms)
        driver_underrun = 1 if (status and status.output_underflow) else 0
        deadline_overrun = 1 if (t_exec_us > DEADLINE_US) else 0
        underrun_flag = 1 if (driver_underrun or deadline_overrun) else 0

        if underrun_flag:
            self.underrun_count += 1

        self.records.append((
            self.callback_count,
            elapsed_sec,
            t_exec_us,
            dt_arrival_us,
            underrun_flag
        ))
        self.callback_count += 1

    def run(self):
        print("========================================================")
        print("  Python Audio Callback Jitter & GC Benchmark (sounddevice)")
        print("========================================================")
        print(f"Sample Rate:     {SAMPLE_RATE} Hz")
        print(f"Block Size:      {BLOCK_SIZE} frames")
        print(f"Deadline (Tmax): {DEADLINE_MS:.4f} ms ({DEADLINE_US:.2f} us)")
        print(f"Target Signal:   {SINE_FREQ} Hz Sine Wave")
        print(f"Duration:        {RUN_DURATION_SEC} seconds")
        print("Memory Policy:   Active Heap Churn (GC Mark-and-Sweep Trigger)")
        print("--------------------------------------------------------")

        # Enable GC explicitly and ensure tracking
        gc.enable()
        
        try:
            with sd.OutputStream(
                samplerate=SAMPLE_RATE,
                blocksize=BLOCK_SIZE,
                channels=1,
                dtype='float32',
                callback=self.audio_callback
            ):
                print("[+] Stream active. Running 10-second benchmark...")
                self._render_live_dashboard(RUN_DURATION_SEC)
        except Exception as e:
            print(f"[-] Error during audio streaming: {e}", file=sys.stderr)
            return False

        print(f"[+] Stream complete. Recorded {len(self.records)} callbacks.")
        print("[+] Exporting metrics to py_metrics.csv...")

        with open("py_metrics.csv", "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["callback_index", "timestamp_sec", "t_exec_us", "dt_arrival_us", "underrun_flag"])
            writer.writerows(self.records)

        texec_list = [r[2] for r in self.records]
        mean_texec = np.mean(texec_list) if texec_list else 0.0
        max_texec = np.max(texec_list) if texec_list else 0.0

        print("[+] Exported to py_metrics.csv successfully.")
        print(f"    - Mean T_exec:  {mean_texec:.2f} us")
        print(f"    - Max T_exec:   {max_texec:.2f} us")
        print(f"    - Underruns:    {self.underrun_count}")
        return True

    def _build_live_dashboard_line(self):
        count = len(self.records)
        if count == 0:
            return "[LIVE] waiting for first callback..."

        latest = self.records[-1]
        recent = self.records[-32:]
        texec_values = [row[2] for row in recent]
        avg_texec = float(np.mean(texec_values)) if texec_values else 0.0
        max_texec = float(np.max(texec_values)) if texec_values else 0.0
        latest_texec = latest[2]
        bar = _format_bar(latest_texec, DEADLINE_US, width=28, fill="#", empty="-")
        tail = _format_tail(texec_values, width=28)

        return (
            f"[LIVE] {count:5d} callbacks | last {latest_texec:7.1f} us "
            f"[{bar}] {latest_texec / DEADLINE_US * 100:5.1f}% | "
            f"avg {avg_texec:7.1f} us | max {max_texec:7.1f} us | "
            f"underruns {self.underrun_count} | {tail}"
        )

    def _render_live_dashboard(self, duration_sec, refresh_hz=10.0):
        start = time.time()
        next_tick = start
        refresh_interval = 1.0 / refresh_hz
        stdout = sys.stdout

        while time.time() - start < duration_sec:
            now = time.time()
            if now < next_tick:
                time.sleep(min(0.02, next_tick - now))
                continue

            next_tick = now + refresh_interval
            render = self._build_live_dashboard_line()
            stdout.write("\r\x1b[2K" + render)
            stdout.flush()

        stdout.write("\n")
        stdout.flush()

if __name__ == "__main__":
    benchmark = PythonJitterBenchmark()
    success = benchmark.run()
    sys.exit(0 if success else 1)
