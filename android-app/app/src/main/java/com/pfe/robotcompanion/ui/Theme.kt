package com.pfe.robotcompanion.ui

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

private val RobotColors = lightColorScheme(
    primary = Color(0xFF176B5B),
    onPrimary = Color.White,
    primaryContainer = Color(0xFFD5F3E9),
    onPrimaryContainer = Color(0xFF073C32),
    secondary = Color(0xFF53636D),
    onSecondary = Color.White,
    secondaryContainer = Color(0xFFE3EBEF),
    tertiary = Color(0xFF9B6500),
    error = Color(0xFFB3261E),
    background = Color(0xFFF7F8FA),
    surface = Color(0xFFFFFFFF),
    surfaceVariant = Color(0xFFE8ECEF),
    outline = Color(0xFF747D83),
)

@Composable
fun RobotCompanionTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = RobotColors,
        typography = MaterialTheme.typography,
        content = content,
    )
}
