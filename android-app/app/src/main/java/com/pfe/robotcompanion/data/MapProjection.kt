package com.pfe.robotcompanion.data

import kotlin.math.cos
import kotlin.math.sin

object MapProjection {
    /** Converts ROS map coordinates to pixels in the untransformed occupancy image. */
    fun worldToPixel(map: MapSnapshot, worldX: Double, worldY: Double): Pair<Float, Float> {
        val dx = worldX - map.originX
        val dy = worldY - map.originY
        val c = cos(-map.originYaw)
        val s = sin(-map.originYaw)
        val localX = c * dx - s * dy
        val localY = s * dx + c * dy
        val pixelX = localX / map.resolution
        val pixelY = map.height - 1.0 - (localY / map.resolution)
        return pixelX.toFloat() to pixelY.toFloat()
    }
}
