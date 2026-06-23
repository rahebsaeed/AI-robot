package com.pfe.robotcompanion.data

import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.json.JSONObject
import java.util.UUID
import java.util.concurrent.Executors
import java.util.concurrent.ScheduledFuture
import java.util.concurrent.TimeUnit
import kotlin.math.min

class RobotWebSocketClient(private val listener: Listener) {
    interface Listener {
        fun onConnectionState(state: ConnectionState, message: String)
        fun onHello(robotName: String)
        fun onMap(map: MapSnapshot)
        fun onPose(pose: RobotPose)
        fun onStatus(status: RobotStatus)
        fun onAcknowledged(requestId: String, state: String)
        fun onProcessing(requestId: String)
        fun onResponse(requestId: String, text: String, state: String, source: String)
        fun onRobotMic(enabled: Boolean)
        fun onError(requestId: String, code: String, message: String)
    }

    private val httpClient = OkHttpClient.Builder()
        .connectTimeout(4, TimeUnit.SECONDS)
        .readTimeout(0, TimeUnit.MILLISECONDS)
        .pingInterval(15, TimeUnit.SECONDS)
        .build()
    private val scheduler = Executors.newSingleThreadScheduledExecutor()
    private var socket: WebSocket? = null
    private var endpoint = RobotEndpoint()
    private var reconnectAttempt = 0
    private var reconnectTask: ScheduledFuture<*>? = null
    private var generation = 0
    @Volatile private var shouldReconnect = false

    @Synchronized
    fun connect(newEndpoint: RobotEndpoint) {
        endpoint = newEndpoint
        shouldReconnect = true
        generation += 1
        val activeGeneration = generation
        reconnectTask?.cancel(false)
        socket?.cancel()
        listener.onConnectionState(ConnectionState.CONNECTING, "Connecting to ${endpoint.host}:${endpoint.port}")
        openSocket(activeGeneration)
    }

    @Synchronized
    fun disconnect() {
        shouldReconnect = false
        generation += 1
        reconnectTask?.cancel(false)
        socket?.close(1000, "user disconnect")
        socket = null
        listener.onConnectionState(ConnectionState.DISCONNECTED, "Disconnected")
    }

    fun close() {
        disconnect()
        scheduler.shutdownNow()
        httpClient.dispatcher.executorService.shutdown()
        httpClient.connectionPool.evictAll()
    }

    data class SendResult(val requestId: String, val sent: Boolean)

    fun sendCommand(text: String, locale: String): SendResult {
        val requestId = UUID.randomUUID().toString()
        val sent = send(JSONObject().apply {
            put("v", 1)
            put("type", "command")
            put("request_id", requestId)
            put("text", text.trim())
            put("locale", locale)
            put("source", "android")
        })
        return SendResult(requestId, sent)
    }

    fun sendStop(): String {
        val requestId = UUID.randomUUID().toString()
        send(JSONObject().apply {
            put("v", 1)
            put("type", "stop")
            put("scope", "all")
            put("request_id", requestId)
        })
        return requestId
    }

    fun cancelSearch(): String {
        val requestId = UUID.randomUUID().toString()
        send(JSONObject().apply {
            put("v", 1)
            put("type", "search_cancel")
            put("request_id", requestId)
        })
        return requestId
    }

    fun sendTeleop(direction: String, speed: Double, active: Boolean) {
        send(JSONObject().apply {
            put("v", 1)
            put("type", "teleop")
            put("request_id", UUID.randomUUID().toString())
            put("direction", direction)
            put("speed", speed)
            put("active", active)
        })
    }

    fun requestMap() {
        send(JSONObject().apply {
            put("v", 1)
            put("type", "map_request")
            put("request_id", UUID.randomUUID().toString())
        })
    }

    fun sendRobotMic(enabled: Boolean): SendResult {
        val requestId = UUID.randomUUID().toString()
        val sent = send(JSONObject().apply {
            put("v", 1)
            put("type", "robot_mic")
            put("request_id", requestId)
            put("enabled", enabled)
        })
        return SendResult(requestId, sent)
    }

    @Synchronized
    private fun openSocket(activeGeneration: Int = generation) {
        val request = Request.Builder()
            .url("ws://${endpoint.host}:${endpoint.port}")
            .build()
        socket = httpClient.newWebSocket(request, SocketListener(activeGeneration))
    }

    private fun send(json: JSONObject): Boolean = socket?.send(json.toString()) == true

    private fun scheduleReconnect(reason: String, failedGeneration: Int) {
        if (!shouldReconnect || failedGeneration != generation) return
        listener.onConnectionState(ConnectionState.ERROR, reason)
        val delay = min(10L, 1L shl min(reconnectAttempt, 3))
        reconnectAttempt += 1
        reconnectTask?.cancel(false)
        reconnectTask = scheduler.schedule({
            if (shouldReconnect) {
                listener.onConnectionState(ConnectionState.CONNECTING, "Reconnecting...")
                generation += 1
                openSocket(generation)
            }
        }, delay, TimeUnit.SECONDS)
    }

    private inner class SocketListener(private val socketGeneration: Int) : WebSocketListener() {
        override fun onOpen(webSocket: WebSocket, response: Response) {
            if (socketGeneration != generation) {
                webSocket.close(1000, "stale connection")
                return
            }
            reconnectAttempt = 0
            webSocket.send(JSONObject().apply {
                put("v", 1)
                put("type", "hello")
                put("client_id", "android-${UUID.randomUUID()}")
                put("token", endpoint.token)
                put("app", "RobotCompanion")
            }.toString())
        }

        override fun onMessage(webSocket: WebSocket, text: String) {
            if (socketGeneration != generation) return
            try {
                parseMessage(JSONObject(text))
            } catch (error: Exception) {
                listener.onError("", "INVALID_SERVER_MESSAGE", error.message ?: "Invalid JSON")
            }
        }

        override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
            if (code == 4003 && socketGeneration == generation) {
                shouldReconnect = false
                listener.onConnectionState(ConnectionState.ERROR, "Pairing token rejected")
            }
            webSocket.close(code, reason)
        }

        override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
            scheduleReconnect("Connection closed: $reason", socketGeneration)
        }

        override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
            scheduleReconnect(t.message ?: "WebSocket connection failed", socketGeneration)
        }
    }

    private fun parseMessage(json: JSONObject) {
        when (json.optString("type")) {
            "hello_ack" -> {
                listener.onConnectionState(ConnectionState.CONNECTED, "Connected")
                listener.onHello(json.optString("robot", "Robot"))
            }
            "map" -> listener.onMap(MapSnapshot(
                id = json.optString("map_id"),
                name = json.optString("name", "map"),
                imageBase64 = json.optString("image_base64"),
                width = json.optInt("width"),
                height = json.optInt("height"),
                resolution = json.optDouble("resolution", 0.05),
                originX = json.optDouble("origin_x", 0.0),
                originY = json.optDouble("origin_y", 0.0),
                originYaw = json.optDouble("origin_yaw", 0.0),
            ))
            "robot_pose" -> {
                val goalJson = json.optJSONObject("navigation_goal")
                val goal = goalJson?.let {
                    NavigationGoal(
                        x = it.optDouble("x"),
                        y = it.optDouble("y"),
                        yaw = it.optDouble("yaw"),
                    )
                }
                listener.onPose(RobotPose(
                    localized = json.optBoolean("localized", false),
                    x = json.optDouble("x", 0.0),
                    y = json.optDouble("y", 0.0),
                    yaw = json.optDouble("yaw", 0.0),
                    poseAge = json.optDouble("pose_age", 0.0),
                    navigationGoal = goal,
                ))
            }
            "status" -> {
                val waypointJson = json.optJSONObject("current_waypoint")
                listener.onStatus(RobotStatus(
                    mode = json.optString("mode", "ready"),
                    phase = json.optString("phase", "ready"),
                    message = json.optString("message"),
                    target = json.optString("target"),
                    waypointIndex = json.optInt("waypoint_index"),
                    waypointTotal = json.optInt("waypoint_total"),
                    currentWaypoint = waypointJson?.let {
                        MapPoint(it.optDouble("x"), it.optDouble("y"))
                    },
                ))
            }
            "ack" -> listener.onAcknowledged(
                json.optString("request_id"),
                json.optString("state", "acknowledged"),
            )
            "processing" -> listener.onProcessing(json.optString("request_id"))
            "response" -> listener.onResponse(
                json.optString("request_id"),
                json.optString("text"),
                json.optString("status", "completed"),
                json.optString("source", "robot"),
            )
            "robot_mic" -> listener.onRobotMic(json.optBoolean("enabled", false))
            "error" -> listener.onError(
                json.optString("request_id"),
                json.optString("code", "ERROR"),
                json.optString("message", "Unknown robot error"),
            )
        }
    }
}
