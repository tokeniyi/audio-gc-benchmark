//============================================================================
// C++ Real-Time Audio Callback Jitter Benchmark (PortAudio)
//============================================================================

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <thread>
#include <array>

#include "portaudio.h"

// Benchmark Constraints & Constants
constexpr double SAMPLE_RATE = 44100.0;
constexpr unsigned long BLOCK_SIZE = 128;
constexpr double SINE_FREQ = 440.0;
constexpr double TWO_PI = 6.28318530717958647692;
constexpr double DEADLINE_MS = (static_cast<double>(BLOCK_SIZE) / SAMPLE_RATE) * 1000.0; // ~2.90249 ms
constexpr double DEADLINE_US = DEADLINE_MS * 1000.0;                                     // ~2902.49 us
constexpr double RUN_DURATION_SEC = 10.0;
constexpr size_t MAX_RECORDS = 10000; // Pre-allocated storage for ~3,445 callbacks
constexpr size_t LIVE_HISTORY = 32;

// Block sizes for sweep testing
const unsigned long BLOCK_SIZES[5] = {128, 64, 256, 512, 1024};
const size_t NUM_BLOCK_SIZES = 5;

// Sample rates for sweep testing
const double SAMPLE_RATES[3] = {44100.0, 48000.0, 96000.0};
const size_t NUM_SAMPLE_RATES = 3;

// Struct for storing callback metrics
struct CallbackMetric {
    double timestamp_sec;
    double t_exec_us;
    double dt_arrival_us;
    int underrun_flag;
};

// Global Pre-allocated Storage (Strict Zero-Allocation in Real-Time Path)
static CallbackMetric g_metrics[MAX_RECORDS];
static std::atomic<size_t> g_metric_count{0};
using clock_type = std::chrono::high_resolution_clock;
static std::chrono::time_point<clock_type> g_benchmark_start;
static std::chrono::time_point<clock_type> g_prev_entry_time;
static bool g_first_callback = true;
static std::atomic<double> g_last_texec_us{0.0};
static std::atomic<double> g_sum_texec_us{0.0};
static std::atomic<double> g_max_texec_us{0.0};
static std::atomic<size_t> g_underrun_count{0};
static std::atomic<size_t> g_live_seq{0};
static std::atomic<double> g_live_texec[LIVE_HISTORY];

// Pre-allocated Audio Synthesis State
struct SynthState {
    double phase = 0.0;
    double phase_increment = (TWO_PI * SINE_FREQ) / SAMPLE_RATE;
    float amplitude = 0.25f;
} g_synth;

// Helper: atomic add
static void atomic_add(std::atomic<double>& target, double value) {
    double current = target.load(std::memory_order_relaxed);
    while (!target.compare_exchange_weak(
                current,
                current + value,
                std::memory_order_relaxed,
                std::memory_order_relaxed)) {
    }
}

// Format a progress bar
static std::string format_bar(double value, double maximum, size_t width = 28) {
    if (maximum <= 0.0) {
        return std::string(width, '-');
    }
    double ratio = value / maximum;
    if (ratio < 0.0) ratio = 0.0;
    if (ratio > 1.0) ratio = 1.0;
    size_t filled = static_cast<size_t>(std::lround(ratio * static_cast<double>(width)));
    if (filled > width) filled = width;
    return std::string(filled, '#') + std::string(width - filled, '-');
}

// Format a sparkline
static std::string format_sparkline(size_t count, size_t seq) {
    if (count == 0) {
        return "";
    }

    const char* blocks = " .:-=+*#%@";
    constexpr size_t block_count = 10;
    size_t span = count < LIVE_HISTORY ? count : LIVE_HISTORY;
    double values[LIVE_HISTORY];

    for (size_t i = 0; i < span; ++i) {
        size_t idx = (seq + LIVE_HISTORY - span + i) % LIVE_HISTORY;
        values[i] = g_live_texec[idx].load(std::memory_order_relaxed);
    }

    double lo = values[0];
    double hi = values[0];
    for (size_t i = 1; i < span; ++i) {
        if (values[i] < lo) lo = values[i];
        if (values[i] > hi) hi = values[i];
    }

    if (hi <= lo) {
        return std::string(span, blocks[block_count - 1]);
    }

    std::string out;
    out.reserve(span);
    for (size_t i = 0; i < span; ++i) {
        double ratio = (values[i] - lo) / (hi - lo);
        size_t block = static_cast<size_t>(std::lround(ratio * static_cast<double>(block_count - 1)));
        if (block >= block_count) block = block_count - 1;
        out.push_back(blocks[block]);
    }
    return out;
}

// Build live dashboard line
static std::string build_live_dashboard_line() {
    size_t count = g_metric_count.load(std::memory_order_acquire);
    if (count == 0) {
        return "[LIVE] waiting for first callback...";
    }

    double latest_texec = g_last_texec_us.load(std::memory_order_relaxed);
    double avg_texec = g_sum_texec_us.load(std::memory_order_relaxed) / static_cast<double>(count);
    double max_texec = g_max_texec_us.load(std::memory_order_relaxed);
    size_t underruns = g_underrun_count.load(std::memory_order_relaxed);
    size_t seq = g_live_seq.load(std::memory_order_relaxed);
    std::string bar = format_bar(latest_texec, DEADLINE_US);
    std::string spark = format_sparkline(count, seq);

    std::ostringstream oss;
    oss.setf(std::ios::fixed);
    oss << std::setprecision(1);
    oss << "[LIVE] " << std::setw(5) << count << " callbacks"
        << " | last " << std::setw(7) << latest_texec << " us "
        << "[" << bar << "] " << std::setw(5) << (latest_texec / DEADLINE_US * 100.0) << "%"
        << " | avg " << std::setw(7) << avg_texec << " us"
        << " | max " << std::setw(7) << max_texec << " us"
        << " | underruns " << underruns
        << " | " << spark;
    return oss.str();
}

// Render live dashboard
static void render_live_dashboard(double duration_sec) {
    using namespace std::chrono;
    auto start = steady_clock::now();
    auto next_tick = start;
    const auto refresh = milliseconds(100);

    while (duration_cast<duration<double>>(steady_clock::now() - start).count() < duration_sec) {
        auto now = steady_clock::now();
        if (now < next_tick) {
            std::this_thread::sleep_for(std::min(milliseconds(20), duration_cast<milliseconds>(next_tick - now)));
            continue;
        }
        next_tick = now + refresh;
        std::cout << "[2K" << build_live_dashboard_line() << std::flush;
    }
    std::cout << std::endl;
}

// Real-Time Audio Callback (Zero Heap Allocations)
static int paCallback(
    const void* /*inputBuffer*/,
    void* outputBuffer,
    unsigned long framesPerBuffer,
    const PaStreamCallbackTimeInfo* /*timeInfo*/,
    PaStreamCallbackFlags statusFlags,
    void* /*userData*/
) {
    // 1. Entry Timestamp
    auto t_entry = clock_type::now();

    // 2. Inter-arrival Delta T Calculation
    double dt_arrival_us = 0.0;
    if (!g_first_callback) {
        dt_arrival_us = std::chrono::duration<double, std::micro>(t_entry - g_prev_entry_time).count();
    } else {
        g_benchmark_start = t_entry;
        g_first_callback = false;
    }
    g_prev_entry_time = t_entry;

    // 3. Audio Synthesis Loop (Phase-continuous Sine)
    float* out = static_cast<float*>(outputBuffer);
    double phase = g_synth.phase;
    const double phase_inc = g_synth.phase_increment;
    const float amp = g_synth.amplitude;

    for (unsigned long i = 0; i < framesPerBuffer; ++i) {
        *out++ = static_cast<float>(sin(phase) * amp);
        phase += phase_inc;
        if (phase >= TWO_PI) {
            phase -= TWO_PI;
        }
    }
    g_synth.phase = phase;

    // 4. Exit Timestamp & Metrics Calculation
    auto t_exit = clock_type::now();
    double t_exec_us = std::chrono::duration<double, std::micro>(t_exit - t_entry).count();
    double elapsed_sec = std::chrono::duration<double>(t_exit - g_benchmark_start).count();

    // Underrun Detection: PortAudio hardware flag OR real-time deadline overrun
    int driver_underrun = (statusFlags & paOutputUnderflow) ? 1 : 0;
    int deadline_overrun = (t_exec_us > DEADLINE_US) ? 1 : 0;
    int underrun_flag = (driver_underrun || deadline_overrun) ? 1 : 0;

    // Record into pre-allocated memory
    size_t idx = g_metric_count.load(std::memory_order_relaxed);
    if (idx < MAX_RECORDS) {
        g_metrics[idx].timestamp_sec = elapsed_sec;
        g_metrics[idx].t_exec_us = t_exec_us;
        g_metrics[idx].dt_arrival_us = dt_arrival_us;
        g_metrics[idx].underrun_flag = underrun_flag;

        g_last_texec_us.store(t_exec_us, std::memory_order_relaxed);
        atomic_add(g_sum_texec_us, t_exec_us);

        double prev_max = g_max_texec_us.load(std::memory_order_relaxed);
        while (t_exec_us > prev_max && !g_max_texec_us.compare_exchange_weak(
                   prev_max,
                   t_exec_us,
                   std::memory_order_relaxed,
                   std::memory_order_relaxed)) {
        }

        if (underrun_flag) {
            g_underrun_count.fetch_add(1, std::memory_order_relaxed);
        }

        size_t slot = g_live_seq.fetch_add(1, std::memory_order_relaxed) % LIVE_HISTORY;
        g_live_texec[slot].store(t_exec_us, std::memory_order_relaxed);

        g_metric_count.store(idx + 1, std::memory_order_release);
    }

    return paContinue;
}

int main() {
    std::cout << "========================================================" << std::endl;
    std::cout << "  C++ Real-Time Audio Callback Jitter Benchmark (PortAudio)" << std::endl;
    std::cout << "========================================================" << std::endl;
    std::cout << "Sample Rate:     " << SAMPLE_RATE << " Hz" << std::endl;
    std::cout << "Block Size:      " << BLOCK_SIZE << " frames" << std::endl;
    std::cout << "Deadline (Tmax): " << DEADLINE_MS << " ms (" << DEADLINE_US << " us)" << std::endl;
    std::cout << "Target Signal:   " << SINE_FREQ << " Hz Sine Wave" << std::endl;
    std::cout << "Duration:        " << RUN_DURATION_SEC << " seconds" << std::endl;
    std::cout << "Memory Policy:   Strict Zero Hot-Path Allocation" << std::endl;
    std::cout << "--------------------------------------------------------" << std::endl;

    PaError err = Pa_Initialize();
    if (err != paNoError) {
        std::cerr << "[-] Error: Pa_Initialize() failed: " << Pa_GetErrorText(err) << std::endl;
        return 1;
    }

    PaDeviceIndex defaultOut = Pa_GetDefaultOutputDevice();
    if (defaultOut == paNoDevice) {
        std::cerr << "[-] Error: No default output device found!" << std::endl;
        Pa_Terminate();
        return 1;
    }

    const PaDeviceInfo* devInfo = Pa_GetDeviceInfo(defaultOut);
    std::cout << "[+] Default output device: " << (devInfo ? devInfo->name : "Unknown") << " (Index: " << defaultOut << ")" << std::endl;

    PaStreamParameters outputParams = {};
    outputParams.device = defaultOut;
    outputParams.channelCount = 1;
    outputParams.sampleFormat = paFloat32;
    outputParams.suggestedLatency = devInfo ? devInfo->defaultLowOutputLatency : 0.050;
    outputParams.hostApiSpecificStreamInfo = nullptr;

    PaStream* stream = nullptr;
    err = Pa_OpenStream(
        &stream,
        nullptr,
        &outputParams,
        SAMPLE_RATE,
        BLOCK_SIZE,
        paClipOff,
        paCallback,
        nullptr
    );

    if (err != paNoError) {
        std::cerr << "[-] Error: Pa_OpenStream() failed: " << Pa_GetErrorText(err) << std::endl;
        Pa_Terminate();
        return 1;
    }

    std::cout << "[+] Starting C++ real-time audio stream..." << std::endl;
    err = Pa_StartStream(stream);
    if (err != paNoError) {
        std::cerr << "[-] Error: Pa_StartStream() failed: " << Pa_GetErrorText(err) << std::endl;
        Pa_CloseStream(stream);
        Pa_Terminate();
        return 1;
    }

    std::cout << "[+] Stream active. Running 10-second benchmark..." << std::endl;
    render_live_dashboard(RUN_DURATION_SEC);

    err = Pa_StopStream(stream);
    if (err != paNoError) {
        std::cerr << "[-] Warning: Pa_StopStream() returned: " << Pa_GetErrorText(err) << std::endl;
    }

    Pa_CloseStream(stream);
    Pa_Terminate();

    size_t metric_count = g_metric_count.load(std::memory_order_acquire);
    std::cout << "[+] Stream complete. Recorded " << metric_count << " callbacks." << std::endl;
    std::cout << "[+] Exporting metrics to cpp_metrics.csv..." << std::endl;

    // Export metrics to CSV, writing to current working directory
    std::string csv_path = "cpp_metrics.csv";
    std::ofstream csv(csv_path);
    if (!csv.is_open()) {
        std::cerr << "[-] Error opening cpp_metrics.csv for writing!" << std::endl;
        return 1;
    }

    csv << "callback_index,timestamp_sec,t_exec_us,dt_arrival_us,underrun_flag
";

    size_t deadline_violations = 0;
    double max_texec = 0.0;
    double sum_texec = 0.0;

    for (size_t i = 0; i < metric_count; ++i) {
        csv << i << ","
            << g_metrics[i].timestamp_sec << ","
            << g_metrics[i].t_exec_us << ","
            << g_metrics[i].dt_arrival_us << ","
            << g_metrics[i].underrun_flag << "
";

        sum_texec += g_metrics[i].t_exec_us;
        if (g_metrics[i].t_exec_us > max_texec) {
            max_texec = g_metrics[i].t_exec_us;
        }
        if (g_metrics[i].underrun_flag) {
            deadline_violations++;
        }
    }
    csv.close();

    std::cout << "[+] Exported to cpp_metrics.csv successfully." << std::endl;
    std::cout << "    - Mean T_exec:  " << (metric_count > 0 ? (sum_texec / metric_count) : 0.0) << " us" << std::endl;
    std::cout << "    - Max T_exec:   " << max_texec << " us" << std::endl;
    std::cout << "    - Underruns:    " << deadline_violations << std::endl;

    return 0;
}
