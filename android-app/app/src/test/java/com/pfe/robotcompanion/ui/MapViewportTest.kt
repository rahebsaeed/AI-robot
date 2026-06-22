package com.pfe.robotcompanion.ui

import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.unit.IntSize
import com.pfe.robotcompanion.data.MapSnapshot
import org.junit.Assert.assertEquals
import org.junit.Test

class MapViewportTest {
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
    fun fittedMapCannotBeDraggedOffScreen() {
        val bounded = MapViewport.boundedPan(map, IntSize(300, 300), 1f, Offset(500f, -500f))
        assertEquals(0f, bounded.x, 0.001f)
        assertEquals(0f, bounded.y, 0.001f)
    }

    @Test
    fun zoomedMapPanIsClampedToItsEdges() {
        val bounded = MapViewport.boundedPan(map, IntSize(300, 300), 2f, Offset(500f, -500f))
        assertEquals(150f, bounded.x, 0.001f)
        assertEquals(-150f, bounded.y, 0.001f)
    }

    @Test
    fun zoomedMapAcceptsHorizontalAndVerticalDrag() {
        val transformed = MapViewport.applyGesture(
            map = map,
            viewport = IntSize(300, 300),
            currentZoom = 2f,
            currentPan = Offset.Zero,
            centroid = Offset(150f, 150f),
            zoomChange = 1f,
            panChange = Offset(-40f, 60f),
        )
        assertEquals(-40f, transformed.pan.x, 0.001f)
        assertEquals(60f, transformed.pan.y, 0.001f)
    }
}
