package com.pfe.robotcompanion

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.pfe.robotcompanion.data.ConnectionState
import com.pfe.robotcompanion.data.ConversationEntry
import com.pfe.robotcompanion.data.MapSnapshot
import com.pfe.robotcompanion.data.RobotEndpoint
import com.pfe.robotcompanion.data.RobotPose
import com.pfe.robotcompanion.data.RobotSettingsRepository
import com.pfe.robotcompanion.data.RobotStatus
import com.pfe.robotcompanion.data.RobotUiState
import com.pfe.robotcompanion.data.RobotWebSocketClient
import com.pfe.robotcompanion.speech.SpeechRecognizerManager
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.collect
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import java.util.UUID

class RobotViewModel(application: Application) : AndroidViewModel(application),
    RobotWebSocketClient.Listener,
    SpeechRecognizerManager.Callback {

    private val settingsRepository = RobotSettingsRepository(application)
    private val webSocketClient = RobotWebSocketClient(this)
    private val speechRecognizer = SpeechRecognizerManager(application, this)
    private val _uiState = MutableStateFlow(RobotUiState())
    val uiState: StateFlow<RobotUiState> = _uiState.asStateFlow()
    private var teleopJob: Job? = null

    init {
        speechRecognizer.prepare()
        viewModelScope.launch {
            settingsRepository.settings.collect { endpoint ->
                _uiState.update { it.copy(endpoint = endpoint) }
            }
        }
    }

    fun updateEndpoint(host: String? = null, port: String? = null, token: String? = null) {
        _uiState.update { state ->
            val parsedPort = port?.toIntOrNull()?.coerceIn(1, 65535) ?: state.endpoint.port
            state.copy(endpoint = state.endpoint.copy(
                host = host ?: state.endpoint.host,
                port = parsedPort,
                token = token ?: state.endpoint.token,
            ))
        }
    }

    fun updateLocale(locale: String) {
        _uiState.update { it.copy(endpoint = it.endpoint.copy(locale = locale)) }
        saveSettings()
    }

    fun updateOfflineSpeech(enabled: Boolean) {
        _uiState.update { it.copy(endpoint = it.endpoint.copy(offlineSpeech = enabled)) }
        saveSettings()
    }

    fun updateAutoSendSpeech(enabled: Boolean) {
        _uiState.update { it.copy(endpoint = it.endpoint.copy(autoSendSpeech = enabled)) }
        saveSettings()
    }

    fun updateTeleopSpeed(speed: Float) {
        _uiState.update {
            it.copy(endpoint = it.endpoint.copy(teleopSpeed = speed.coerceIn(0.2f, 1.0f)))
        }
    }

    fun saveTeleopSpeed() = saveSettings()

    fun connect() {
        val endpoint = _uiState.value.endpoint
        if (endpoint.host.isBlank()) {
            _uiState.update { it.copy(connectionMessage = "Robot IP address is required") }
            return
        }
        viewModelScope.launch { settingsRepository.save(endpoint) }
        webSocketClient.connect(endpoint)
    }

    fun disconnect() {
        stopTeleop()
        webSocketClient.disconnect()
    }

    fun setTranscript(text: String) {
        _uiState.update { it.copy(transcript = text) }
    }

    fun sendTranscript() {
        val text = _uiState.value.transcript.trim()
        if (text.isEmpty()) return
        if (_uiState.value.connection != ConnectionState.CONNECTED) {
            _uiState.update { it.copy(speechMode = "Connect to the robot before sending") }
            return
        }
        val result = webSocketClient.sendCommand(text.take(MAX_COMMAND_LENGTH), _uiState.value.endpoint.locale)
        if (!result.sent) {
            addConversation(ConversationEntry(
                result.requestId,
                "system",
                "Command was not sent because the connection is unavailable.",
                "error",
            ))
            _uiState.update { it.copy(speechMode = "Send failed. Reconnect and try again") }
            return
        }
        addConversation(ConversationEntry(result.requestId, "user", text.take(MAX_COMMAND_LENGTH), "sending"))
        _uiState.update {
            it.copy(
                transcript = "",
                speechPartial = "",
                speechMode = "Sent to robot; waiting for acknowledgement",
            )
        }
    }

    fun emergencyStop() {
        stopTeleop()
        val requestId = webSocketClient.sendStop()
        addConversation(ConversationEntry(requestId, "user", "STOP ALL", "executing"))
    }

    fun cancelSearch() {
        val requestId = webSocketClient.cancelSearch()
        addConversation(ConversationEntry(requestId, "user", "Cancel current search", "executing"))
    }

    fun requestMap() = webSocketClient.requestMap()

    fun startSpeech() {
        val state = _uiState.value
        _uiState.update { it.copy(speechMode = "Starting speech recognition...") }
        speechRecognizer.start(state.endpoint.locale, state.endpoint.offlineSpeech)
    }

    fun stopSpeech() = speechRecognizer.stop()

    fun cancelSpeech() = speechRecognizer.cancel()

    fun onMicrophonePermissionDenied() {
        _uiState.update { it.copy(
            isListening = false,
            speechMode = "Microphone permission was denied. Enable it in Android app settings.",
        ) }
    }

    fun startTeleop(direction: String) {
        if (_uiState.value.connection != ConnectionState.CONNECTED) return
        teleopJob?.cancel()
        _uiState.update { it.copy(teleopDirection = direction) }
        val speed = _uiState.value.endpoint.teleopSpeed.toDouble()
        webSocketClient.sendTeleop(direction, speed = speed, active = true)
        teleopJob = viewModelScope.launch {
            while (true) {
                delay(TELEOP_HEARTBEAT_MS)
                webSocketClient.sendTeleop(direction, speed = speed, active = true)
            }
        }
    }

    fun stopTeleop(direction: String? = null) {
        if (direction != null && _uiState.value.teleopDirection != direction) return
        teleopJob?.cancel()
        teleopJob = null
        _uiState.update { it.copy(teleopDirection = null) }
        webSocketClient.sendTeleop("stop", speed = 0.0, active = false)
    }

    private fun addConversation(entry: ConversationEntry) {
        _uiState.update { state ->
            state.copy(conversation = (state.conversation + entry).takeLast(30))
        }
    }

    private fun saveSettings() {
        val endpoint = _uiState.value.endpoint
        viewModelScope.launch { settingsRepository.save(endpoint) }
    }

    override fun onConnectionState(state: ConnectionState, message: String) {
        if (state != ConnectionState.CONNECTED) {
            teleopJob?.cancel()
            teleopJob = null
            _uiState.update { it.copy(teleopDirection = null) }
        }
        _uiState.update { current -> current.copy(connection = state, connectionMessage = message) }
    }

    override fun onHello(robotName: String) {
        _uiState.update { it.copy(robotName = robotName) }
    }

    override fun onMap(map: MapSnapshot) {
        _uiState.update { it.copy(map = map) }
    }

    override fun onPose(pose: RobotPose) {
        _uiState.update { state ->
            state.copy(pose = if (pose.localized) pose else state.pose)
        }
    }

    override fun onStatus(status: RobotStatus) {
        _uiState.update { it.copy(status = status) }
    }

    override fun onAcknowledged(requestId: String, state: String) {
        _uiState.update { current ->
            val visibleCommand = current.conversation.any {
                it.id == requestId && it.role == "user"
            }
            if (!visibleCommand) return@update current
            current.copy(
                speechMode = "Robot acknowledged command: $state",
                conversation = current.conversation.map {
                    if (it.id == requestId && it.role == "user") it.copy(state = state) else it
                },
            )
        }
    }

    override fun onProcessing(requestId: String) {
        _uiState.update { state ->
            state.copy(conversation = state.conversation.map {
                if (it.id == requestId) it.copy(state = "processing") else it
            })
        }
    }

    override fun onResponse(requestId: String, text: String, state: String, source: String) {
        addConversation(ConversationEntry(
            id = if (requestId.isBlank()) UUID.randomUUID().toString() else requestId,
            role = "robot",
            text = text,
            state = state,
        ))
    }

    override fun onError(requestId: String, code: String, message: String) {
        addConversation(ConversationEntry(
            id = requestId.ifBlank { UUID.randomUUID().toString() },
            role = "system",
            text = "$code: $message",
            state = "error",
        ))
    }

    override fun onReady(mode: String) {
        _uiState.update { it.copy(speechMode = mode) }
    }

    override fun onPartial(text: String) {
        _uiState.update { it.copy(speechPartial = text, transcript = text) }
    }

    override fun onFinal(text: String) {
        _uiState.update {
            it.copy(
                speechPartial = "",
                transcript = text,
                speechMode = if (it.endpoint.autoSendSpeech) "Speech recognized; sending..." else "Speech recognized; review and tap Send",
            )
        }
        if (_uiState.value.endpoint.autoSendSpeech) sendTranscript()
    }

    override fun onListeningChanged(listening: Boolean) {
        _uiState.update { it.copy(isListening = listening) }
    }

    override fun onError(message: String) {
        _uiState.update { it.copy(isListening = false, speechMode = message) }
    }

    override fun onCleared() {
        teleopJob?.cancel()
        speechRecognizer.destroy()
        webSocketClient.close()
        super.onCleared()
    }

    private companion object {
        const val MAX_COMMAND_LENGTH = 500
        const val TELEOP_HEARTBEAT_MS = 200L
    }
}
