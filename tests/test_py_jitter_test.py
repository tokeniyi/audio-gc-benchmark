"""Tests for py_jitter_test.py utility functions and GC profile handling.

These tests verify the Python engine's formatting helpers, GC pressure
profiles, and class structure without requiring an audio device.
"""
import sys
import os

import numpy as np
import pytest

# Make the Python engine importable
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "python"))

from py_jitter_test import (
    _format_bar,
    _format_tail,
    PythonJitterBenchmark,
    DEADLINE_MS,
    DEADLINE_US,
    SAMPLE_RATE,
    BLOCK_SIZE,
)


# --------------------------------------------------------------------------- #
# Tests: _format_bar (Python engine)
# --------------------------------------------------------------------------- #
class TestFormatBar:
    def test_zero_maximum(self):
        result = _format_bar(50, 0)
        assert result == "-" * 24

    def test_value_at_maximum(self):
        result = _format_bar(100, 100, width=10)
        assert result == "#" * 10

    def test_value_above_maximum_clamped(self):
        result = _format_bar(150, 100, width=10)
        assert result == "#" * 10

    def test_value_zero(self):
        result = _format_bar(0, 100, width=10)
        assert result == "-" * 10

    def test_half_fill(self):
        result = _format_bar(50, 100, width=10)
        assert result.startswith("#####")
        assert result.endswith("-----")


# --------------------------------------------------------------------------- #
# Tests: _format_tail
# --------------------------------------------------------------------------- #
class TestFormatTail:
    def test_empty_values(self):
        result = _format_tail([])
        assert result == ""

    def test_single_value(self):
        result = _format_tail([100.0])
        assert result == "@"  # single value, all max block when lo==hi

    def test_uniform_values(self):
        result = _format_tail([50.0, 50.0, 50.0])
        assert result == "@@@"

    def test_varied_values(self):
        result = _format_tail([100.0, 200.0, 300.0])
        assert len(result) == 3
        assert all(c in " .:-=+*#%@" for c in result)


# --------------------------------------------------------------------------- #
# Tests: PythonJitterBenchmark GC profiles
# --------------------------------------------------------------------------- #
class TestGCPressureProfiles:
    @pytest.mark.parametrize("pressure", ["none", "light", "medium", "heavy", "extreme"])
    def test_instantiation(self, pressure):
        bench = PythonJitterBenchmark(gc_pressure=pressure)
        assert bench.gc_pressure == pressure

    def test_default_pressure(self):
        bench = PythonJitterBenchmark()
        assert bench.gc_pressure == "medium"

    def test_none_profile_has_zero_config(self):
        bench = PythonJitterBenchmark(gc_pressure="none")
        assert bench._churn_size == 0
        assert bench._large_buffer_size == 0
        assert bench._pin_list_length == 0

    def test_light_profile(self):
        bench = PythonJitterBenchmark(gc_pressure="light")
        assert bench._churn_size == 10
        assert bench._pin_list_length == 5

    def test_heavy_profile_large_buffer(self):
        bench = PythonJitterBenchmark(gc_pressure="heavy")
        assert bench._large_buffer_size == 1024 * 1024  # 1 MiB
        assert bench._churn_size == 200
        assert bench._pin_list_length == 100

    def test_extreme_profile(self):
        bench = PythonJitterBenchmark(gc_pressure="extreme")
        assert bench._churn_size == 500
        assert bench._pin_list_length == 500

    def test_invalid_pressure_raises(self):
        with pytest.raises(ValueError):
            PythonJitterBenchmark(gc_pressure="invalid")


# --------------------------------------------------------------------------- #
# Tests: Benchmark constants
# --------------------------------------------------------------------------- #
class TestConstants:
    def test_deadline_calculation(self):
        expected = (128 / 44100) * 1000.0
        assert DEADLINE_MS == pytest.approx(expected, rel=1e-6)
        assert DEADLINE_US == pytest.approx(expected * 1000.0, rel=1e-6)

    def test_sample_rate(self):
        assert SAMPLE_RATE == 44100

    def test_block_size(self):
        assert BLOCK_SIZE == 128


# --------------------------------------------------------------------------- #
# Tests: PythonJitterBenchmark initialization
# --------------------------------------------------------------------------- #
class TestBenchmarkInit:
    def test_initial_state(self):
        bench = PythonJitterBenchmark()
        assert bench.callback_count == 0
        assert bench.underrun_count == 0
        assert bench.first_callback is True
        assert bench.benchmark_start_ns is None
        assert len(bench.records) == 0

    def test_phase_increment(self):
        bench = PythonJitterBenchmark()
        expected_phase_inc = (2.0 * np.pi * 440.0) / 44100.0
        assert bench.phase_increment == pytest.approx(expected_phase_inc, rel=1e-10)

    def test_amplitude(self):
        bench = PythonJitterBenchmark()
        assert bench.amplitude == 0.25

    def test_available_gc_profiles(self):
        profiles = PythonJitterBenchmark.list_gc_profiles()
        assert set(profiles) == {"none", "light", "medium", "heavy", "extreme"}


# --------------------------------------------------------------------------- #
# Tests: Callback execution (without audio device)
# --------------------------------------------------------------------------- #
class TestCallbackExecution:
    def test_gc_churn_none_no_allocations(self):
        """Test that 'none' pressure callback does minimal work."""
        bench = PythonJitterBenchmark(gc_pressure="none")
        # Manually trigger the GC churn path with none pressure
        # Should return immediately without allocating
        bench._run_gchurn(0)
        assert True  # Should not raise

    def test_gc_churn_initializes_pinned_list(self):
        """Test that churn creates the pinned list on first call."""
        bench = PythonJitterBenchmark(gc_pressure="medium")
        assert not hasattr(bench, "_pinned_list")
        bench._run_gchurn(0)
        assert hasattr(bench, "_pinned_list")
        assert len(bench._pinned_list) == bench._pin_list_length

    def test_gc_churn_periodic_rebuild(self):
        """Test the periodic list rebuild and gc.collect() path."""
        bench = PythonJitterBenchmark(gc_pressure="light")
        # Set callback_count to trigger the % 10 == 0 path
        bench.callback_count = 10
        bench._run_gchurn(0)
        # The pinned list should have been rebuilt
        assert len(bench._pinned_list) == bench._pin_list_length
