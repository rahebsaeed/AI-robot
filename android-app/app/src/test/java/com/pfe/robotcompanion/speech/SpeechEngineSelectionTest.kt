package com.pfe.robotcompanion.speech

import org.junit.Assert.assertEquals
import org.junit.Test

class SpeechEngineSelectionTest {
    @Test
    fun huaweiWithoutAndroidRecognitionUsesEmbeddedFallback() {
        assertEquals(
            SpeechEngine.EMBEDDED_VOSK,
            selectSpeechEngine(
                onDeviceAvailable = false,
                systemAvailable = false,
                preferOffline = false,
            ),
        )
    }

    @Test
    fun automaticModeUsesSystemServiceWhenAvailable() {
        assertEquals(
            SpeechEngine.ANDROID_SYSTEM,
            selectSpeechEngine(
                onDeviceAvailable = false,
                systemAvailable = true,
                preferOffline = false,
            ),
        )
    }

    @Test
    fun forcedOfflineDoesNotUseOnlineSystemService() {
        assertEquals(
            SpeechEngine.EMBEDDED_VOSK,
            selectSpeechEngine(
                onDeviceAvailable = false,
                systemAvailable = true,
                preferOffline = true,
            ),
        )
    }
}
