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
     * without adding processing risk on top.
     */
    val processor: AudioProcessor = PassthroughProcessor()
}
