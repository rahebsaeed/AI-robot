package com.pfe.robotcompanion.speech

internal enum class SpeechEngine {
    ANDROID_ON_DEVICE,
    ANDROID_SYSTEM,
    EMBEDDED_VOSK,
}

internal fun selectSpeechEngine(
    onDeviceAvailable: Boolean,
    systemAvailable: Boolean,
    preferOffline: Boolean,
): SpeechEngine = when {
    onDeviceAvailable -> SpeechEngine.ANDROID_ON_DEVICE
    !preferOffline && systemAvailable -> SpeechEngine.ANDROID_SYSTEM
    else -> SpeechEngine.EMBEDDED_VOSK
}
