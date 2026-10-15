package com.muteharam.hook

/**
 * The processor the hooks talk to. Normally the delay/sync experiment
 * ([DelayTestProcessor]); when `muteharam_mode` is "remote" or "local" it runs the
 * real music removal ([SeparationHost]) instead. Switching is live.
 */
class ModeProcessor : AudioProcessor {
    private val test = DelayTestProcessor()

    override val latencyMs: Int
        get() = if (HookSettings.separating) SeparationHost.latencyMs() else test.latencyMs

    override val clockShiftMs: Int
        get() = if (HookSettings.separating) SeparationHost.latencyMs() else test.clockShiftMs

    override val shiftClock: Boolean
        get() = test.shiftClock   // the "noclock" switch applies in every mode

    override fun process(
        pcm16: ShortArray, offset: Int, length: Int, sampleRateHz: Int, channelCount: Int, streamId: Int
    ) {
        if (HookSettings.separating) {
            // If the pipeline can't be created the buffer is left as it is (pass-through).
            SeparationHost.processPcm16(streamId, pcm16, offset, length, sampleRateHz, channelCount)
        } else {
            test.process(pcm16, offset, length, sampleRateHz, channelCount, streamId)
        }
    }

    override fun reset(streamId: Int) {
        test.reset(streamId)
        SeparationHost.reset(streamId)
    }
}
