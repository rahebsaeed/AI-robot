package com.pfe.robotcompanion.ui

import android.graphics.BitmapFactory
import android.util.Base64
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.rememberTransformableState
import androidx.compose.foundation.gestures.transformable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Map
import androidx.compose.material.icons.filled.MyLocation
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.IntSize
import androidx.compose.ui.unit.dp
import androidx.compose.ui.layout.onSizeChanged
import com.pfe.robotcompanion.data.MapProjection
import com.pfe.robotcompanion.data.MapSnapshot
import com.pfe.robotcompanion.data.RobotPose
import com.pfe.robotcompanion.data.RobotStatus
import kotlin.math.cos
import kotlin.math.max
import kotlin.math.min
import kotlin.math.sin

@Composable
fun RobotMap(
    map: MapSnapshot?,
    pose: RobotPose,
    status: RobotStatus,
    modifier: Modifier = Modifier,
) {
    var zoom by remember(map?.id) { mutableFloatStateOf(1f) }
    var pan by remember(map?.id) { mutableStateOf(Offset.Zero) }
    var viewportSize by remember { mutableStateOf(IntSize.Zero) }
    val transformState = rememberTransformableState { centroid, zoomChange, panChange, _ ->
        val transform = MapViewport.applyGesture(
            map = map,
            viewport = viewportSize,
            currentZoom = zoom,
            currentPan = pan,
            centroid = centroid,
            zoomChange = zoomChange,
            panChange = panChange,
        )
        zoom = transform.zoom
        pan = transform.pan
    }
    val image = remember(map?.id) {
        map?.imageBase64?.let { encoded ->
            runCatching {
                val bytes = Base64.decode(encoded, Base64.DEFAULT)
                BitmapFactory.decodeByteArray(bytes, 0, bytes.size)?.asImageBitmap()
            }.getOrNull()
        }
    }
    val robotColor = MaterialTheme.colorScheme.primary

    Box(
        modifier = modifier
            .background(Color(0xFFE8EEEC))
            .onSizeChanged { size ->
                viewportSize = size
                pan = MapViewport.boundedPan(map, size, zoom, pan)
            }
            .transformable(transformState),
    ) {
        Canvas(Modifier.fillMaxSize()) {
            if (map == null || image == null || map.width <= 0 || map.height <= 0) {
                return@Canvas
            }

            val fitScale = min(size.width / map.width, size.height / map.height)
            val scale = fitScale * zoom
            val imageWidth = map.width * scale
            val imageHeight = map.height * scale
            val origin = Offset(
                (size.width - imageWidth) / 2f + pan.x,
                (size.height - imageHeight) / 2f + pan.y,
            )

            drawImage(
                image = image,
                dstOffset = IntOffset(origin.x.toInt(), origin.y.toInt()),
                dstSize = IntSize(imageWidth.toInt().coerceAtLeast(1), imageHeight.toInt().coerceAtLeast(1)),
            )

            fun screenPoint(worldX: Double, worldY: Double): Offset {
                val pixel = MapProjection.worldToPixel(map, worldX, worldY)
                return Offset(origin.x + pixel.first * scale, origin.y + pixel.second * scale)
            }

            pose.navigationGoal?.let { goal ->
                val point = screenPoint(goal.x, goal.y)
                drawCircle(Color(0xFF1565C0), radius = 10f, center = point, style = Stroke(4f))
                drawLine(Color(0xFF1565C0), point - Offset(14f, 0f), point + Offset(14f, 0f), 3f)
                drawLine(Color(0xFF1565C0), point - Offset(0f, 14f), point + Offset(0f, 14f), 3f)
            }

            status.currentWaypoint?.let { waypoint ->
                drawCircle(Color(0xFFE18A00), radius = 7f, center = screenPoint(waypoint.x, waypoint.y))
            }

            if (pose.localized) {
                val center = screenPoint(pose.x, pose.y)
                val headingLength = 22f
                val tip = Offset(
                    center.x + cos(pose.yaw).toFloat() * headingLength,
                    center.y - sin(pose.yaw).toFloat() * headingLength,
                )
                drawCircle(Color.White, radius = 13f, center = center)
                drawCircle(robotColor, radius = 10f, center = center)
                drawLine(Color(0xFF072F28), center, tip, strokeWidth = 5f)
            }
        }

        if (map == null || image == null || map.width <= 0 || map.height <= 0) {
            androidx.compose.foundation.layout.Column(
                modifier = Modifier.align(Alignment.Center),
                horizontalAlignment = Alignment.CenterHorizontally,
            ) {
                CircularProgressIndicator(modifier = Modifier.size(34.dp), strokeWidth = 3.dp)
                androidx.compose.foundation.layout.Spacer(Modifier.size(12.dp))
                Icon(Icons.Default.Map, contentDescription = null, tint = MaterialTheme.colorScheme.onSurfaceVariant)
                Text(
                    "Waiting for robot map",
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }

        if (map != null && image != null) {
            SurfaceMapButton(
                onClick = { zoom = 1f; pan = Offset.Zero },
                modifier = Modifier.align(Alignment.BottomEnd),
            )
        }
    }
}

internal object MapViewport {
    data class Transform(val zoom: Float, val pan: Offset)

    fun applyGesture(
        map: MapSnapshot?,
        viewport: IntSize,
        currentZoom: Float,
        currentPan: Offset,
        centroid: Offset,
        zoomChange: Float,
        panChange: Offset,
    ): Transform {
        val newZoom = (currentZoom * zoomChange).coerceIn(1f, 6f)
        val ratio = newZoom / currentZoom
        val viewportCenter = Offset(viewport.width / 2f, viewport.height / 2f)
        val focusAdjustedPan =
            currentPan * ratio + (centroid - viewportCenter) * (1f - ratio) + panChange
        return Transform(newZoom, boundedPan(map, viewport, newZoom, focusAdjustedPan))
    }

    fun boundedPan(
        map: MapSnapshot?,
        viewport: IntSize,
        zoom: Float,
        candidate: Offset,
    ): Offset {
        if (map == null || map.width <= 0 || map.height <= 0 || viewport.width <= 0 || viewport.height <= 0) {
            return Offset.Zero
        }
        val fitScale = min(
            viewport.width.toFloat() / map.width,
            viewport.height.toFloat() / map.height,
        )
        val horizontalLimit = max(0f, (map.width * fitScale * zoom - viewport.width) / 2f)
        val verticalLimit = max(0f, (map.height * fitScale * zoom - viewport.height) / 2f)
        return Offset(
            candidate.x.coerceIn(-horizontalLimit, horizontalLimit),
            candidate.y.coerceIn(-verticalLimit, verticalLimit),
        )
    }
}

@Composable
private fun SurfaceMapButton(onClick: () -> Unit, modifier: Modifier = Modifier) {
    androidx.compose.material3.Surface(
        modifier = modifier.padding(10.dp),
        shape = androidx.compose.foundation.shape.CircleShape,
        color = MaterialTheme.colorScheme.surface.copy(alpha = 0.92f),
        shadowElevation = 3.dp,
    ) {
        IconButton(onClick = onClick) {
            Icon(Icons.Default.MyLocation, contentDescription = "Recenter map")
        }
    }
}
