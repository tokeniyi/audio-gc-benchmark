#include <iostream>
#include <fstream>
#include <chrono>
#include <cmath>
#include <atomic>
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

struct CallbackMetric {
    double timestamp_sec;
    double t_exec_us;
    double dt_arrival_us;
    int underrun_flag;
};

// Global Pre-allocated Storage (Strict Zero-Allocation in Real-Time Path)
static CallbackMetric g_metrics[MAX_RECORDS];
static size_t g_metric_count = 0;
using clock_type = std::chrono::high_resolution_clock;
static std::chrono::time_point<clock_type> g_benchmark_start;
static std::chrono::time_point<clock_type> g_prev_entry_time;
static bool g_first_callback = true;

// Pre-allocated Audio Synthesis State
struct SynthState {
    double phase = 0.0;
    double phase_increment = (TWO_PI * SINE_FREQ) / SAMPLE_RATE;
    float amplitude = 0.25f;
} g_synth;

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
    if (g_metric_count < MAX_RECORDS) {
        g_metrics[g_metric_count].timestamp_sec = elapsed_sec;
        g_metrics[g_metric_count].t_exec_us = t_exec_us;
        g_metrics[g_metric_count].dt_arrival_us = dt_arrival_us;
        g_metrics[g_metric_count].underrun_flag = underrun_flag;
        g_metric_count++;
    }

    return paContinue;
}

int main() {
    std::cout << "========================================================\n"
              << "  C++ Real-Time Audio Callback Jitter Benchmark (PortAudio)\n"
              << "========================================================\n"
              << "Sample Rate:     " << SAMPLE_RATE << " Hz\n"
              << "Block Size:      " << BLOCK_SIZE << " frames\n"
              << "Deadline (Tmax): " << DEADLINE_MS << " ms (" << DEADLINE_US << " us)\n"
              << "Target Signal:   " << SINE_FREQ << " Hz Sine Wave\n"
              << "Duration:        " << RUN_DURATION_SEC << " seconds\n"
              << "Memory Policy:   Strict Zero Hot-Path Allocation\n"
              << "--------------------------------------------------------" << std::endl;

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

    // Sleep on master thread while audio thread processes real-time callbacks
    Pa_Sleep(static_cast<long>(RUN_DURATION_SEC * 1000.0));

    err = Pa_StopStream(stream);
    if (err != paNoError) {
        std::cerr << "[-] Warning: Pa_StopStream() returned: " << Pa_GetErrorText(err) << std::endl;
    }

    Pa_CloseStream(stream);
    Pa_Terminate();

    std::cout << "[+] Stream complete. Recorded " << g_metric_count << " callbacks." << std::endl;
    std::cout << "[+] Exporting metrics to cpp_metrics.csv..." << std::endl;

    std::ofstream csv("cpp_metrics.csv");
    if (!csv.is_open()) {
        std::cerr << "[-] Error opening cpp_metrics.csv for writing!\n";
        return 1;
    }

    csv << "callback_index,timestamp_sec,t_exec_us,dt_arrival_us,underrun_flag\n";
    size_t deadline_violations = 0;
    double max_texec = 0.0;
    double sum_texec = 0.0;

    for (size_t i = 0; i < g_metric_count; ++i) {
        csv << i << ","
            << g_metrics[i].timestamp_sec << ","
            << g_metrics[i].t_exec_us << ","
            << g_metrics[i].dt_arrival_us << ","
            << g_metrics[i].underrun_flag << "\n";

        sum_texec += g_metrics[i].t_exec_us;
        if (g_metrics[i].t_exec_us > max_texec) {
            max_texec = g_metrics[i].t_exec_us;
        }
        if (g_metrics[i].underrun_flag) {
            deadline_violations++;
        }
    }
    csv.close();

    std::cout << "[+] Exported to cpp_metrics.csv successfully.\n";
    std::cout << "    - Mean T_exec:  " << (g_metric_count > 0 ? (sum_texec / g_metric_count) : 0.0) << " us\n";
    std::cout << "    - Max T_exec:   " << max_texec << " us\n";
    std::cout << "    - Underruns:    " << deadline_violations << "\n";

    return 0;
}
