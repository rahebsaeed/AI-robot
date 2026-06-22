package com.pfe.robotcompanion.data

import android.content.Context
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.floatPreferencesKey
import androidx.datastore.preferences.core.intPreferencesKey
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map

private val Context.robotSettingsDataStore by preferencesDataStore(name = "robot_connection")

class RobotSettingsRepository(private val context: Context) {
    private object Keys {
        val host = stringPreferencesKey("host")
        val port = intPreferencesKey("port")
        val token = stringPreferencesKey("token")
        val locale = stringPreferencesKey("locale")
        val offlineSpeech = booleanPreferencesKey("offline_speech")
        val autoSendSpeech = booleanPreferencesKey("auto_send_speech")
        val teleopSpeed = floatPreferencesKey("teleop_speed")
    }

    val settings: Flow<RobotEndpoint> = context.robotSettingsDataStore.data.map { values ->
        RobotEndpoint(
            host = values[Keys.host] ?: "192.168.50.196",
            port = values[Keys.port] ?: 8765,
            token = values[Keys.token] ?: "",
            locale = values[Keys.locale] ?: "en-US",
            offlineSpeech = values[Keys.offlineSpeech] ?: false,
            autoSendSpeech = values[Keys.autoSendSpeech] ?: true,
            teleopSpeed = (values[Keys.teleopSpeed] ?: 0.55f).coerceIn(0.2f, 1.0f),
        )
    }

    suspend fun save(endpoint: RobotEndpoint) {
        context.robotSettingsDataStore.edit { values ->
            values[Keys.host] = endpoint.host.trim()
            values[Keys.port] = endpoint.port
            values[Keys.token] = endpoint.token
            values[Keys.locale] = endpoint.locale
            values[Keys.offlineSpeech] = endpoint.offlineSpeech
            values[Keys.autoSendSpeech] = endpoint.autoSendSpeech
            values[Keys.teleopSpeed] = endpoint.teleopSpeed
        }
    }
}
