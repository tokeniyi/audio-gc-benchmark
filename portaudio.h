#ifndef PORTAUDIO_H
#define PORTAUDIO_H

#ifdef __cplusplus
extern "C" {
#endif

typedef int PaError;
typedef enum PaErrorCode {
    paNoError = 0,
    paNotInitialized = -10000,
    paUnanticipatedHostError,
    paInvalidChannelCount,
    paInvalidSampleRate,
    paInvalidDevice,
    paInvalidFlag,
    paSampleFormatNotSupported,
    paBadIODeviceCombination,
    paInsufficientMemory,
    paBufferTooBig,
    paBufferTooSmall,
    paNullCallback,
    paBadStreamPtr,
    paTimedOut,
    paInternalError,
    paDeviceUnavailable,
    paIncompatibleHostApiSpecificStreamInfo,
    paStreamIsStopped,
    paStreamIsNotStopped,
    paInputOverflowed,
    paOutputUnderflowed,
    paHostApiNotFound,
    paInvalidHostApi,
    paCanNotReadFromACallbackStream,
    paCanNotWriteToACallbackStream,
    paCanNotReadFromAnOutputOnlyStream,
    paCanNotWriteToAnInputOnlyStream,
    paIncompatibleStreamHostApi,
    paBadBufferPtr
} PaErrorCode;

typedef void PaStream;
typedef unsigned long PaSampleFormat;
typedef double PaTime;
typedef int PaDeviceIndex;

#define paNoDevice ((PaDeviceIndex)-1)
#define paUseHostApiSpecificDeviceSpecification ((PaDeviceIndex)-2)

#define paFloat32        ((PaSampleFormat) 0x00000001)
#define paInt32          ((PaSampleFormat) 0x00000002)
#define paInt24          ((PaSampleFormat) 0x00000004)
#define paInt16          ((PaSampleFormat) 0x00000008)
#define paInt8           ((PaSampleFormat) 0x00000010)
#define paUInt8          ((PaSampleFormat) 0x00000020)
#define paCustomFormat   ((PaSampleFormat) 0x00010000)
#define paNonInterleaved ((PaSampleFormat) 0x80000000)

typedef unsigned long PaStreamFlags;
#define paNoFlag          ((PaStreamFlags) 0)
#define paClipOff         ((PaStreamFlags) 0x00000001)
#define paDitherOff       ((PaStreamFlags) 0x00000002)
#define paNeverDropInput  ((PaStreamFlags) 0x00000004)
#define paPrimeOutputBuffersUsingStreamCallback ((PaStreamFlags) 0x00000008)
#define paPlatformSpecificFlags ((PaStreamFlags)0xFFFF0000)

typedef unsigned long PaStreamCallbackFlags;
#define paInputUnderflow   ((PaStreamCallbackFlags) 0x00000001)
#define paInputOverflow    ((PaStreamCallbackFlags) 0x00000002)
#define paOutputUnderflow  ((PaStreamCallbackFlags) 0x00000004)
#define paOutputOverflow   ((PaStreamCallbackFlags) 0x00000008)
#define paPrimingOutput    ((PaStreamCallbackFlags) 0x00000010)

typedef struct PaStreamCallbackTimeInfo {
    double inputBufferAdcTime;
    double currentTime;
    double outputBufferDacTime;
} PaStreamCallbackTimeInfo;

typedef enum PaStreamCallbackResult {
    paContinue = 0,
    paComplete = 1,
    paAbort = 2
} PaStreamCallbackResult;

typedef int PaStreamCallback(
    const void *input, void *output,
    unsigned long frameCount,
    const PaStreamCallbackTimeInfo* timeInfo,
    PaStreamCallbackFlags statusFlags,
    void *userData );

typedef struct PaDeviceInfo {
    int structVersion;
    const char *name;
    int hostApi;
    int maxInputChannels;
    int maxOutputChannels;
    PaTime defaultLowInputLatency;
    PaTime defaultLowOutputLatency;
    PaTime defaultHighInputLatency;
    PaTime defaultHighOutputLatency;
    double defaultSampleRate;
} PaDeviceInfo;

typedef struct PaStreamParameters {
    PaDeviceIndex device;
    int channelCount;
    PaSampleFormat sampleFormat;
    PaTime suggestedLatency;
    void *hostApiSpecificStreamInfo;
} PaStreamParameters;

PaError Pa_Initialize( void );
PaError Pa_Terminate( void );
PaError Pa_GetVersion( void );
const char* Pa_GetVersionText( void );
const char *Pa_GetErrorText( PaError errorCode );
PaDeviceIndex Pa_GetDeviceCount( void );
PaDeviceIndex Pa_GetDefaultOutputDevice( void );
PaDeviceIndex Pa_GetDefaultInputDevice( void );
const PaDeviceInfo* Pa_GetDeviceInfo( PaDeviceIndex device );

PaError Pa_OpenStream(
    PaStream** stream,
    const PaStreamParameters *inputParameters,
    const PaStreamParameters *outputParameters,
    double sampleRate,
    unsigned long framesPerBuffer,
    PaStreamFlags streamFlags,
    PaStreamCallback *streamCallback,
    void *userData );

PaError Pa_OpenDefaultStream(
    PaStream** stream,
    int numInputChannels,
    int numOutputChannels,
    PaSampleFormat sampleFormat,
    double sampleRate,
    unsigned long framesPerBuffer,
    PaStreamCallback *streamCallback,
    void *userData );

PaError Pa_CloseStream( PaStream *stream );
PaError Pa_StartStream( PaStream *stream );
PaError Pa_StopStream( PaStream *stream );
PaError Pa_AbortStream( PaStream *stream );
PaError Pa_IsStreamStopped( PaStream *stream );
PaError Pa_IsStreamActive( PaStream *stream );
PaTime Pa_GetStreamTime( PaStream *stream );
double Pa_GetStreamCpuLoad( PaStream *stream );
PaError Pa_Sleep( long msec );

#ifdef __cplusplus
}
#endif

#endif /* PORTAUDIO_H */
