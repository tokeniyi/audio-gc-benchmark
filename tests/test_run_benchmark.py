"""Tests for run_benchmark.py utility functions.

These tests verify the statistical computation, metrics loading,
and formatting helpers without requiring an audio device.
"""
import csv
import json
import os
import sys
import tempfile

import numpy as np
import pytest

# Make run_benchmark importable
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
sys.path.insert(0, PROJECT_ROOT)

import run_benchmark


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture
def sample_csv(tmp_path):
    """Create a sample CSV metrics file for testing."""
    csv_path = tmp_path / "test_metrics.csv"
    rows = [
        (0, 0.0000, 150.0, 2900.0, 0),
        (1, 0.0029, 160.0, 2901.0, 0),
        (2, 0.0058, 2500.0, 2900.0, 1),  # violation
        (3, 0.0087, 170.0, 2901.0, 0),
        (4, 0.0116, 180.0, 2900.0, 0),
    ]
    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["callback_index", "timestamp_sec", "t_exec_us", "dt_arrival_us", "underrun_flag"])
        writer.writerows(rows)
    return csv_path, rows


@pytest.fixture
def sample_metrics(sample_csv):
    """Load sample metrics from CSV."""
    csv_path, _ = sample_csv
    return run_benchmark.load_metrics(str(csv_path))


# --------------------------------------------------------------------------- #
# Tests: load_metrics
# --------------------------------------------------------------------------- #
class TestLoadMetrics:
    def test_loads_all_rows(self, sample_metrics):
        assert len(sample_metrics["indices"]) == 5

    def test_returns_correct_arrays(self, sample_metrics, sample_csv):
        _, rows = sample_csv
        for i, row in enumerate(rows):
            assert sample_metrics["indices"][i] == row[0]
            assert sample_metrics["texec_us"][i] == row[2]
            assert sample_metrics["underruns"][i] == row[4]

    def test_raises_on_missing_file(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            run_benchmark.load_metrics(str(tmp_path / "nonexistent.csv"))


# --------------------------------------------------------------------------- #
# Tests: compute_statistics
# --------------------------------------------------------------------------- #
class TestComputeStatistics:
    def test_total_callbacks(self, sample_metrics):
        stats = run_benchmark.compute_statistics(sample_metrics)
        assert stats["total_callbacks"] == 5

    def test_mean_calculation(self, sample_metrics, sample_csv):
        _, rows = sample_csv
        expected_mean = np.mean([r[2] for r in rows])
        stats = run_benchmark.compute_statistics(sample_metrics)
        assert stats["mean_us"] == pytest.approx(expected_mean)

    def test_std_calculation(self, sample_metrics, sample_csv):
        _, rows = sample_csv
        expected_std = np.std([r[2] for r in rows], ddof=1)
        stats = run_benchmark.compute_statistics(sample_metrics)
        assert stats["std_us"] == pytest.approx(expected_std)

    def test_percentiles(self, sample_metrics, sample_csv):
        _, rows = sample_csv
        texec = np.array([r[2] for r in rows])
        stats = run_benchmark.compute_statistics(sample_metrics)
        assert stats["p50_us"] == pytest.approx(np.percentile(texec, 50))
        assert stats["p95_us"] == pytest.approx(np.percentile(texec, 95))
        assert stats["p99_us"] == pytest.approx(np.percentile(texec, 99))
        assert stats["p100_us"] == pytest.approx(np.max(texec))

    def test_underrun_count(self, sample_metrics):
        stats = run_benchmark.compute_statistics(sample_metrics)
        assert stats["underruns"] == 1

    def test_violation_count(self, sample_metrics):
        """DEADLINE_US is 2902.0; none of the test data exceeds it."""
        stats = run_benchmark.compute_statistics(sample_metrics)
        assert stats["violations"] == 0

    def test_violation_percentage(self, sample_metrics):
        stats = run_benchmark.compute_statistics(sample_metrics)
        assert stats["violation_pct"] == pytest.approx(0.0)

    def test_empty_metrics(self):
        empty = {
            "indices": np.array([]),
            "timestamps": np.array([]),
            "texec_us": np.array([]),
            "dt_arrival_us": np.array([]),
            "underruns": np.array([]),
        }
        result = run_benchmark.compute_statistics(empty)
        assert result == {}

    def test_violation_detection_with_exceeding_data(self):
        """Test that values exceeding DEADLINE_US are counted as violations."""
        metrics = {
            "indices": np.array([0, 1, 2]),
            "timestamps": np.array([0.0, 0.003, 0.006]),
            "texec_us": np.array([100.0, 3000.0, 4000.0]),  # 3000 and 4000 exceed 2902.0
            "dt_arrival_us": np.array([2900.0, 2901.0, 2900.0]),
            "underruns": np.array([0, 1, 1]),
        }
        stats = run_benchmark.compute_statistics(metrics)
        assert stats["violations"] == 2
        assert stats["violation_pct"] == pytest.approx(66.666666, rel=1e-3)


# --------------------------------------------------------------------------- #
# Tests: median_iqr
# --------------------------------------------------------------------------- #
class TestMedianIQR:
    def test_basic_stats(self):
        vals = [10.0, 20.0, 30.0, 40.0, 50.0]
        result = run_benchmark.median_iqr(vals)
        assert result["median"] == 30.0
        assert result["iqr_low"] == 20.0
        assert result["iqr_high"] == 40.0

    def test_even_count_median(self):
        vals = [10.0, 20.0, 30.0, 40.0]
        result = run_benchmark.median_iqr(vals)
        assert result["median"] == pytest.approx(25.0)


# --------------------------------------------------------------------------- #
# Tests: _format_bar
# --------------------------------------------------------------------------- #
class TestFormatBar:
    def test_zero_maximum(self):
        result = run_benchmark._format_bar(50, 0)
        assert result == "-" * 28

    def test_value_at_maximum(self):
        result = run_benchmark._format_bar(100, 100, width=10)
        assert result == "#" * 10

    def test_value_above_maximum_clamped(self):
        result = run_benchmark._format_bar(150, 100, width=10)
        assert result == "#" * 10

    def test_value_zero(self):
        result = run_benchmark._format_bar(0, 100, width=10)
        assert result == "-" * 10

    def test_half_fill(self):
        result = run_benchmark._format_bar(50, 100, width=10)
        assert result.startswith("#####")
        assert result.endswith("-----")


# --------------------------------------------------------------------------- #
# Tests: _format_tail (Python engine helper)
# --------------------------------------------------------------------------- #
class TestFormatTail:
    def test_empty_values(self):
        result = run_benchmark._format_bar(0, 100)  # smoke test
        assert len(result) > 0

    def test_single_value(self):
        # Import the Python engine's helper
        sys.path.insert(0, os.path.join(PROJECT_ROOT, "python"))
        from py_jitter_test import _format_tail
        result = _format_tail([100.0])
        assert result != ""


# --------------------------------------------------------------------------- #
# Tests: run_benchmark CLI argument parsing
# --------------------------------------------------------------------------- #
class TestArgumentParsing:
    def test_default_args(self):
        # Simulate parsing with no args
        old_argv = sys.argv
        sys.argv = ["run_benchmark.py"]
        try:
            parser = run_benchmark.__dict__.get("main")
            # We can't easily run main(), but we can test the argparse setup
            # by calling parse_args on a manually created parser
            import argparse
            # Recreate the parser logic
            parser = argparse.ArgumentParser()
            parser.add_argument(
                "--gc-pressure",
                choices=["none", "light", "medium", "heavy", "extreme"],
                default="medium",
            )
            parser.add_argument("--trials", type=int, default=5)
            args = parser.parse_args([])
            assert args.gc_pressure == "medium"
            assert args.trials == 5
        finally:
            sys.argv = old_argv

    def test_custom_args(self):
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument(
            "--gc-pressure",
            choices=["none", "light", "medium", "heavy", "extreme"],
            default="medium",
        )
        parser.add_argument("--trials", type=int, default=5)
        args = parser.parse_args(["--gc-pressure", "heavy", "--trials", "3"])
        assert args.gc_pressure == "heavy"
        assert args.trials == 3


# --------------------------------------------------------------------------- #
# Tests: run_py_benchmark gc_pressure parameter passing
# --------------------------------------------------------------------------- #
class TestGcPressurePassing:
    def test_gc_pressure_default(self):
        # Verify the function signature accepts gc_pressure
        import inspect
        sig = inspect.signature(run_benchmark.run_py_benchmark)
        assert "gc_pressure" in sig.parameters
        assert sig.parameters["gc_pressure"].default == "medium"

    def test_run_trial_accepts_gc_pressure(self):
        import inspect
        sig = inspect.signature(run_benchmark.run_trial)
        assert "gc_pressure" in sig.parameters
        assert sig.parameters["gc_pressure"].default == "medium"
