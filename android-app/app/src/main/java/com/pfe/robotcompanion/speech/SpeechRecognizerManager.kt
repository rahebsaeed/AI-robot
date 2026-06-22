package com.pfe.robotcompanion.speech

import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import org.json.JSONObject
import org.vosk.Model
import org.vosk.Recognizer
import org.vosk.android.SpeechService
import org.vosk.android.StorageService
import java.util.Locale

class SpeechRecognizerManager(
    private val context: Context,
    private val callback: Callback,
) {
    interface Callback {
        fun onReady(mode: String)
        fun onPartial(text: String)
        fun onFinal(text: String)
        fun onListeningChanged(listening: Boolean)
        fun onError(message: String)
    }

    private val mainHandler = Handler(Looper.getMainLooper())
    private var androidRecognizer: SpeechRecognizer? = null
    private var voskModel: Model? = null
    private var voskModelLoading = false
    private var voskService: SpeechService? = null
    private var listening = false
    private var usingVosk = false
    private var pendingVoskStart = false
    private var suppressNextAndroidError = false
    private var resultDelivered = false
    private var latestVoskPartial = ""
    private var destroyed = false

    fun prepare() {
        mainHandler.post {
            if (destroyed || voskModel != null || voskModelLoading) return@post
            val androidRecognizerAvailable = SpeechRecognizer.isRecognitionAvailable(context) ||
                (Build.VERSION.SDK_INT >= 31 && SpeechRecognizer.isOnDeviceRecognitionAvailable(context))
            if (!androidRecognizerAvailable) loadVoskModel()
        }
    }

    fun start(locale: String, preferOffline: Boolean) {
        mainHandler.post {
            if (destroyed) return@post
            if (listening) return@post
            releaseActiveRecognizer(cancel = true)
            resultDelivered = false

            val onDeviceAvailable = Build.VERSION.SDK_INT >= 31 &&
                SpeechRecognizer.isOnDeviceRecognitionAvailable(context)
            val systemAvailable = SpeechRecognizer.isRecognitionAvailable(context)

            when (selectSpeechEngine(onDeviceAvailable, systemAvailable, preferOffline)) {
                SpeechEngine.ANDROID_ON_DEVICE -> startAndroid(locale, onDevice = true, preferOffline = true)
                SpeechEngine.ANDROID_SYSTEM -> startAndroid(locale, onDevice = false, preferOffline = false)
                SpeechEngine.EMBEDDED_VOSK -> startVosk(locale)
            }
        }
    }

    fun stop() {
        mainHandler.post {
            if (!listening) return@post
            if (usingVosk) {
                if (pendingVoskStart) {
                    cancelVosk()
                } else {
                    voskService?.stop()
                }
            } else {
                androidRecognizer?.stopListening()
            }
        }
    }

    fun cancel() {
        mainHandler.post {
            suppressNextAndroidError = true
            releaseActiveRecognizer(cancel = true)
            setListening(false)
        }
    }

    fun destroy() {
        mainHandler.post {
            destroyed = true
            releaseActiveRecognizer(cancel = true)
            voskModel?.close()
            voskModel = null
        }
    }

    private fun startAndroid(locale: String, onDevice: Boolean, preferOffline: Boolean) {
        usingVosk = false
        try {
            androidRecognizer = if (onDevice && Build.VERSION.SDK_INT >= 31) {
                SpeechRecognizer.createOnDeviceSpeechRecognizer(context)
            } else {
                SpeechRecognizer.createSpeechRecognizer(context)
            }
            androidRecognizer?.setRecognitionListener(AndroidRecognitionCallbacks())
        } catch (error: Exception) {
            startVosk(locale)
            return
        }

        callback.onReady(if (onDevice) "Android on-device recognizer" else "Android system recognizer")
        val intent = recognitionIntent(locale, preferOffline)
        suppressNextAndroidError = false
        setListening(true)
        try {
            androidRecognizer?.startListening(intent)
        } catch (error: Exception) {
            androidRecognizer?.destroy()
            androidRecognizer = null
            setListening(false)
            startVosk(locale)
        }
    }

    private fun startVosk(locale: String) {
        if (!locale.lowercase(Locale.ROOT).startsWith("en")) {
            callback.onError(
                "This Huawei-compatible offline model supports English. Set Speech locale to en-US.",
            )
            return
        }

        usingVosk = true
        pendingVoskStart = true
        latestVoskPartial = ""
        callback.onReady("Preparing embedded offline speech model...")
        setListening(true)

        val readyModel = voskModel
        if (readyModel != null) {
            beginVoskListening(readyModel)
            return
        }
        loadVoskModel()
    }

    private fun loadVoskModel() {
        if (voskModel != null || voskModelLoading || destroyed) return
        voskModelLoading = true
        StorageService.unpack(
            context,
            VOSK_ASSET_PATH,
            VOSK_STORAGE_PATH,
            { model ->
                voskModelLoading = false
                if (destroyed) {
                    model.close()
                    return@unpack
                }
                voskModel = model
                if (pendingVoskStart) beginVoskListening(model)
            },
            { error ->
                voskModelLoading = false
                if (pendingVoskStart) {
                    pendingVoskStart = false
                    setListening(false)
                    callback.onError("Embedded speech model could not be prepared: ${error.message}")
                }
            },
        )
    }

    private fun beginVoskListening(model: Model) {
        if (!pendingVoskStart) return
        try {
            val recognizer = Recognizer(model, VOSK_SAMPLE_RATE)
            voskService = SpeechService(recognizer, VOSK_SAMPLE_RATE)
            pendingVoskStart = false
            callback.onReady("Embedded offline recognizer (Huawei compatible)")
            if (voskService?.startListening(VoskRecognitionCallbacks(), VOSK_TIMEOUT_MS) != true) {
                throw IllegalStateException("Offline recognizer is already active")
            }
        } catch (error: Exception) {
            cancelVosk()
            callback.onError(error.message ?: "Could not start the embedded speech recognizer")
        }
    }

    private fun recognitionIntent(locale: String, preferOffline: Boolean) =
        Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
            putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
            putExtra(RecognizerIntent.EXTRA_LANGUAGE, locale.ifBlank { Locale.getDefault().toLanguageTag() })
            putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true)
            putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 3)
            putExtra(RecognizerIntent.EXTRA_PREFER_OFFLINE, preferOffline)
        }

    private fun releaseActiveRecognizer(cancel: Boolean) {
        if (cancel) androidRecognizer?.cancel()
        androidRecognizer?.destroy()
        androidRecognizer = null
        cancelVosk()
    }

    private fun cancelVosk() {
        pendingVoskStart = false
        voskService?.cancel()
        voskService?.shutdown()
        voskService = null
        usingVosk = false
    }

    private fun setListening(value: Boolean) {
        if (listening == value) return
        listening = value
        callback.onListeningChanged(value)
    }

    private fun deliverFinal(text: String) {
        val cleaned = text.trim()
        if (resultDelivered || cleaned.isEmpty()) return
        resultDelivered = true
        setListening(false)
        callback.onFinal(cleaned)
    }

    private inner class AndroidRecognitionCallbacks : RecognitionListener {
        override fun onReadyForSpeech(params: Bundle?) = Unit
        override fun onBeginningOfSpeech() = Unit
        override fun onRmsChanged(rmsdB: Float) = Unit
        override fun onBufferReceived(buffer: ByteArray?) = Unit
        override fun onEndOfSpeech() = Unit

        override fun onError(error: Int) {
            setListening(false)
            if (suppressNextAndroidError) {
                suppressNextAndroidError = false
                return
            }
            callback.onError(errorMessage(error))
        }

        override fun onResults(results: Bundle?) {
            setListening(false)
            val text = results
                ?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                ?.firstOrNull()
                .orEmpty()
            if (text.isNotBlank()) deliverFinal(text) else callback.onError("Speech recognition returned no text")
        }

        override fun onPartialResults(partialResults: Bundle?) {
            val text = partialResults
                ?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                ?.firstOrNull()
                .orEmpty()
            if (text.isNotBlank()) callback.onPartial(text)
        }

        override fun onEvent(eventType: Int, params: Bundle?) = Unit
    }

    private inner class VoskRecognitionCallbacks : org.vosk.android.RecognitionListener {
        override fun onPartialResult(hypothesis: String) {
            parseVoskText(hypothesis, "partial").takeIf { it.isNotBlank() }?.let {
                latestVoskPartial = it
                callback.onPartial(it)
            }
        }

        override fun onResult(hypothesis: String) {
            val text = parseVoskText(hypothesis, "text")
            if (text.isNotBlank()) {
                deliverFinal(text)
                cancelVosk()
            }
        }

        override fun onFinalResult(hypothesis: String) {
            val text = parseVoskText(hypothesis, "text").ifBlank { latestVoskPartial }
            if (text.isNotBlank()) deliverFinal(text)
            cancelVosk()
        }

        override fun onError(exception: Exception) {
            setListening(false)
            cancelVosk()
            callback.onError(exception.message ?: "Embedded speech recognition failed")
        }

        override fun onTimeout() {
            val text = latestVoskPartial
            setListening(false)
            cancelVosk()
            if (text.isNotBlank()) deliverFinal(text) else callback.onError("No speech detected")
        }
    }

    private fun parseVoskText(json: String, key: String): String =
        runCatching { JSONObject(json).optString(key) }.getOrDefault("").trim()

    private fun errorMessage(error: Int): String = when (error) {
        SpeechRecognizer.ERROR_AUDIO -> "Microphone recording error"
        SpeechRecognizer.ERROR_CLIENT -> "Speech recognizer client error"
        SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS -> "Microphone permission is required"
        SpeechRecognizer.ERROR_LANGUAGE_NOT_SUPPORTED -> "Selected language is not supported"
        SpeechRecognizer.ERROR_LANGUAGE_UNAVAILABLE -> "Selected language model is not downloaded"
        SpeechRecognizer.ERROR_NETWORK, SpeechRecognizer.ERROR_NETWORK_TIMEOUT -> "Speech recognition network error"
        SpeechRecognizer.ERROR_NO_MATCH -> "No speech could be recognized"
        SpeechRecognizer.ERROR_RECOGNIZER_BUSY -> "Speech recognizer is busy"
        SpeechRecognizer.ERROR_SERVER, SpeechRecognizer.ERROR_SERVER_DISCONNECTED -> "Speech recognition service unavailable"
        SpeechRecognizer.ERROR_SPEECH_TIMEOUT -> "No speech detected"
        SpeechRecognizer.ERROR_TOO_MANY_REQUESTS -> "Too many speech recognition requests"
        else -> "Speech recognition error ($error)"
    }

    private companion object {
        const val VOSK_ASSET_PATH = "model-en-us"
        const val VOSK_STORAGE_PATH = "speech-models"
        const val VOSK_SAMPLE_RATE = 16_000.0f
        const val VOSK_TIMEOUT_MS = 15_000
    }
}
