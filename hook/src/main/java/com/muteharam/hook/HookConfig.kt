package com.muteharam.hook

object HookConfig {
    /**
     * Package names this module hooks into. Each one must also be
     * individually enabled for this module inside the LSPosed Manager
     * app (module hooks don't apply automatically just by being
     * listed here -- LSPosed requires explicit per-app scope).
     *
     * YouTube's package name is fixed. CineHD's is a placeholder --
     * replace it with the real applicationId. Find it by: Settings ->
     * Apps -> CineHD -> Advanced/App details -> "com.whatever.it.is",
     * or `adb shell pm list packages | grep -i cine` while it's
     * installed.
     */
    val TARGET_PACKAGES = setOf(
        "com.google.android.youtube",
        "com.cinehd.app" // TODO: replace with CineHD's real package name
    )

    /**
     * Swap this for a real separation model once it's built and
     * benchmarked. Passthrough proves the hook pipeline itself works
     * without adding processing risk (or latency) on top first.
     *
     * Once a model is ready, switch to something like:
     *
     *   StreamingAudioProcessor(sampleRateHz = 44100) { windowChunk ->
     *       // run inference on windowChunk (120ms of float samples,
     *       // -1f..1f range), return the processed chunk of the same
     *       // length with music suppressed.
     *       realModel.runInference(windowChunk)
     *   }
     *
     * See StreamingAudioProcessor's doc comment for the latency
     * budget that window size needs to respect.
     */
    val processor: AudioProcessor = DelayTestProcessor() // passthrough unless debug.muteharam.delay_ms is set
}
