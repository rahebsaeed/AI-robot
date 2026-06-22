package com.pfe.robotcompanion.data

import org.junit.Assert.assertEquals
import org.junit.Test
import kotlin.math.PI

class MapProjectionTest {
    private val map = MapSnapshot(
        id = "test",
        name = "test",
        imageBase64 = "",
        width = 800,
        height = 800,
        resolution = 0.05,
        originX = -20.0,
        originY = -20.0,
        originYaw = 0.0,
    )

    @Test
    fun convertsRosOriginToBottomLeftImagePixel() {
        val (x, y) = MapProjection.worldToPixel(map, -20.0, -20.0)
        assertEquals(0f, x, 0.001f)
        assertEquals(799f, y, 0.001f)
    }

    @Test
    fun convertsPositiveWorldCoordinatesUsingResolution() {
        val (x, y) = MapProjection.worldToPixel(map, -19.0, -19.0)
        assertEquals(20f, x, 0.001f)
        assertEquals(779f, y, 0.001f)
    }

    @Test
    fun appliesMapOriginYawBeforeProjection() {
        val rotated = map.copy(originX = 0.0, originY = 0.0, originYaw = PI / 2.0)
        val (x, y) = MapProjection.worldToPixel(rotated, 0.0, 1.0)
        assertEquals(20f, x, 0.001f)
        assertEquals(799f, y, 0.001f)
    }
}
