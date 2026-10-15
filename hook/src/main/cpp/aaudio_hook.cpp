// Native counterpart of AudioTrackHook for apps whose audio goes through
// AAudio (e.g. CineHD: a WebView/Chromium player). Two hooks, same idea as the
// Java side:
//   1. wrap the stream's data callback and delay the PCM it produces
//   2. shift AAudioStream_getTimestamp() so the player thinks the audio
//      pipeline is longer by the same amount and holds the video back
//      (Chromium: AAudioStreamWrapper::GetOutputDelay)
// Config (delay/shift/noclock) is pushed from Java via nativeSetConfig().

#include <aaudio/AAudio.h>
#include <android/log.h>
#include <dlfcn.h>
#include <jni.h>

#include <atomic>
#include <cmath>
#include <cstdint>
#include <mutex>
#include <unordered_map>
#include <vector>

#include "shadowhook.h"

#define TAG "MuteHaramHook"
#define LOGI(...) __android_log_print(ANDROID_LOG_INFO, TAG, __VA_ARGS__)

namespace {

std::atomic<int> g_delayMs{0};
std::atomic<int> g_shiftMs{-1};  // -1 = same as delay
std::atomic<int> g_noClock{0};

struct CbCtx {
    AAudioStream_dataCallback cb;
    void* user;
};

struct Ring {
    std::vector<float> f;
    std::vector<int16_t> s;
    size_t pos = 0;
    int rate = 0, ch = 0, delay = -1;
    aaudio_format_t fmt = AAUDIO_FORMAT_UNSPECIFIED;
    bool logged = false;
    double inSq = 0, outSq = 0;
    long cnt = 0;
};

std::mutex g_mu;
std::unordered_map<AAudioStream*, Ring> g_rings;

using SetDataCbFn = void (*)(AAudioStreamBuilder*, AAudioStream_dataCallback, void*);
using GetTsFn = aaudio_result_t (*)(AAudioStream*, clockid_t, int64_t*, int64_t*);
using CloseFn = aaudio_result_t (*)(AAudioStream*);

SetDataCbFn orig_setDataCb = nullptr;
GetTsFn orig_getTs = nullptr;
CloseFn orig_close = nullptr;

template <typename T>
void delayLine(std::vector<T>& ring, size_t& pos, T* data, size_t n) {
    size_t p = pos;
    const size_t sz = ring.size();
    for (size_t i = 0; i < n; i++) {
        T old = ring[p];
        ring[p] = data[i];
        data[i] = old;
        if (++p == sz) p = 0;
    }
    pos = p;
}

template <typename T>
double sumSq(const T* d, size_t n, double scale) {
    double s = 0;
    for (size_t i = 0; i < n; i++) {
        double v = d[i] * scale;
        s += v * v;
    }
    return s;
}

void processStream(AAudioStream* stream, void* data, int32_t frames) {
    if (AAudioStream_getDirection(stream) != AAUDIO_DIRECTION_OUTPUT) return;
    const aaudio_format_t fmt = AAudioStream_getFormat(stream);
    if (fmt != AAUDIO_FORMAT_PCM_FLOAT && fmt != AAUDIO_FORMAT_PCM_I16) return;
    const int ch = AAudioStream_getChannelCount(stream);
    const int rate = AAudioStream_getSampleRate(stream);
    const int d = g_delayMs.load();
    const size_t n = (size_t)frames * ch;

    std::lock_guard<std::mutex> lk(g_mu);
    Ring& r = g_rings[stream];
    if (!r.logged) {
        r.logged = true;
        LOGI("aaudio stream %p FIRST CALLBACK: fmt=%d(1=I16,2=float) ch=%d rate=%d frames/cb=%d",
             (void*)stream, (int)fmt, ch, rate, frames);
    }

    const bool isFloat = fmt == AAUDIO_FORMAT_PCM_FLOAT;
    const double scale = isFloat ? 1.0 : 1.0 / 32768.0;
    r.inSq += isFloat ? sumSq((float*)data, n, scale) : sumSq((int16_t*)data, n, scale);

    if (d > 0) {
        const size_t need = (size_t)((int64_t)rate * d / 1000) * ch;
        if (need > 0) {
            if (r.delay != d || r.rate != rate || r.ch != ch || r.fmt != fmt) {
                r.f.clear();
                r.s.clear();
                if (isFloat) r.f.assign(need, 0.f); else r.s.assign(need, 0);
                r.pos = 0;
                r.delay = d; r.rate = rate; r.ch = ch; r.fmt = fmt;
            }
            if (isFloat) delayLine(r.f, r.pos, (float*)data, n);
            else delayLine(r.s, r.pos, (int16_t*)data, n);
        }
    } else if (r.delay != 0) {
        r.f.clear(); r.s.clear(); r.pos = 0; r.delay = 0;
    }

    r.outSq += isFloat ? sumSq((float*)data, n, scale) : sumSq((int16_t*)data, n, scale);
    r.cnt += (long)n;
    if (r.cnt >= (long)rate * ch / 2) {  // ~every 500 ms of audio
        LOGI("aaudio lvl in=%.4f out=%.4f delay=%d", std::sqrt(r.inSq / r.cnt),
             std::sqrt(r.outSq / r.cnt), d);
        r.inSq = r.outSq = 0;
        r.cnt = 0;
    }
}

aaudio_data_callback_result_t wrappedCallback(AAudioStream* stream, void* user, void* data,
                                              int32_t frames) {
    CbCtx* c = (CbCtx*)user;
    aaudio_data_callback_result_t res = c->cb(stream, c->user, data, frames);
    processStream(stream, data, frames);
    return res;
}

void proxy_setDataCb(AAudioStreamBuilder* b, AAudioStream_dataCallback cb, void* user) {
    if (cb == nullptr) {
        orig_setDataCb(b, cb, user);
        return;
    }
    LOGI("AAudioStreamBuilder_setDataCallback: wrapping callback");
    orig_setDataCb(b, wrappedCallback, new CbCtx{cb, user});  // ctx lives as long as the stream
}

aaudio_result_t proxy_getTs(AAudioStream* s, clockid_t clk, int64_t* pos, int64_t* ns) {
    aaudio_result_t r = orig_getTs(s, clk, pos, ns);
    if (r == AAUDIO_OK && pos != nullptr && !g_noClock.load() &&
        AAudioStream_getDirection(s) == AAUDIO_DIRECTION_OUTPUT) {
        int shift = g_shiftMs.load();
        if (shift < 0) shift = g_delayMs.load();
        if (shift > 0) {
            int64_t frames = (int64_t)AAudioStream_getSampleRate(s) * shift / 1000;
            *pos -= frames;
        }
    }
    return r;
}

aaudio_result_t proxy_close(AAudioStream* s) {
    {
        std::lock_guard<std::mutex> lk(g_mu);
        g_rings.erase(s);
    }
    return orig_close(s);
}

}  // namespace

extern "C" JNIEXPORT jint JNICALL
Java_com_muteharam_hook_NativeBridge_nativeInit(JNIEnv*, jclass) {
    int rc = shadowhook_init(SHADOWHOOK_MODE_UNIQUE, false);
    if (rc != 0) {
        LOGI("shadowhook_init failed rc=%d", rc);
        return rc;
    }
    dlopen("libaaudio.so", RTLD_NOW);
    int fails = 0;
    auto hook = [&](const char* sym, void* proxy, void** orig) {
        void* stub = shadowhook_hook_sym_name("libaaudio.so", sym, proxy, orig);
        if (stub == nullptr) {
            fails++;
            LOGI("hook %s FAILED (errno %d: %s)", sym, shadowhook_get_errno(),
                 shadowhook_to_errmsg(shadowhook_get_errno()));
        } else {
            LOGI("hooked %s", sym);
        }
    };
    hook("AAudioStreamBuilder_setDataCallback", (void*)proxy_setDataCb, (void**)&orig_setDataCb);
    hook("AAudioStream_getTimestamp", (void*)proxy_getTs, (void**)&orig_getTs);
    hook("AAudioStream_close", (void*)proxy_close, (void**)&orig_close);
    return fails;
}

extern "C" JNIEXPORT void JNICALL
Java_com_muteharam_hook_NativeBridge_nativeSetConfig(JNIEnv*, jclass, jint delayMs, jint shiftMs,
                                                     jint noClock) {
    if (g_delayMs.load() != delayMs || g_shiftMs.load() != shiftMs || g_noClock.load() != noClock) {
        LOGI("native settings: delay_ms=%d shift_ms=%d noclock=%d", delayMs, shiftMs, noClock);
    }
    g_delayMs = delayMs;
    g_shiftMs = shiftMs;
    g_noClock = noClock;
}
