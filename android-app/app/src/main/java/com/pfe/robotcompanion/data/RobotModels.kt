package com.pfe.robotcompanion.data

enum class ConnectionState {
    DISCONNECTED,
    CONNECTING,
    CONNECTED,
    ERROR,
}

data class RobotEndpoint(
    val host: String = "192.168.50.196",
    val port: Int = 8765,
    val token: String = "",
    val locale: String = "en-US",
    val offlineSpeech: Boolean = false,
    val autoSendSpeech: Boolean = true,
    val teleopSpeed: Float = 0.55f,
)

data class MapSnapshot(
    val id: String,
    val name: String,
    val imageBase64: String,
    val width: Int,
    val height: Int,
    val resolution: Double,
    val originX: Double,
    val originY: Double,
    val originYaw: Double,
)

data class MapPoint(val x: Double, val y: Double)

data class NavigationGoal(
    val x: Double,
    val y: Double,
    val yaw: Double,
)

data class RobotPose(
    val localized: Boolean = false,
    val x: Double = 0.0,
    val y: Double = 0.0,
    val yaw: Double = 0.0,
    val poseAge: Double = 0.0,
    val navigationGoal: NavigationGoal? = null,
)

data class RobotStatus(
    val mode: String = "offline",
    val phase: String = "disconnected",
    val message: String = "Not connected",
    val target: String = "",
    val waypointIndex: Int = 0,
    val waypointTotal: Int = 0,
    val currentWaypoint: MapPoint? = null,
)

data class ConversationEntry(
    val id: String,
    val role: String,
    val text: String,
    val state: String = "completed",
    val timestamp: Long = System.currentTimeMillis(),
)

data class RobotUiState(
    val endpoint: RobotEndpoint = RobotEndpoint(),
    val connection: ConnectionState = ConnectionState.DISCONNECTED,
    val connectionMessage: String = "Disconnected",
    val robotName: String = "Rosmaster X3 Plus",
    val map: MapSnapshot? = null,
    val pose: RobotPose = RobotPose(),
    val status: RobotStatus = RobotStatus(),
    val transcript: String = "",
    val speechPartial: String = "",
    val isListening: Boolean = false,
    val robotMicEnabled: Boolean = false,
    val teleopDirection: String? = null,
    val speechMode: String = "Tap the microphone to speak",
    val conversation: List<ConversationEntry> = emptyList(),
)
