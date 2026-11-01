package lmeditor.desktop.ui

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.height
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp

/** A visual page guide; its margin bands follow the manuscript's actual insets. */
@Composable
fun DocumentRuler(modifier: Modifier = Modifier, pageMargin: Dp = 56.dp) {
    val paper = MaterialTheme.colorScheme.surface
    val margin = MaterialTheme.colorScheme.outline.copy(alpha = 0.12f)
    val tick = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.45f)
    Canvas(modifier.height(22.dp)) {
        val inset = pageMargin.toPx().coerceIn(0f, size.width / 2)
        drawRect(paper)
        drawRect(margin, size = Size(inset, size.height))
        drawRect(margin, topLeft = Offset(size.width - inset, 0f), size = Size(inset, size.height))
        // Eight subdivisions per inch of an 8.5 inch sheet. This scales with
        // the paper, rather than claiming a physical display measurement.
        for (index in 0..68) {
            val x = size.width * index / 68f
            val length = when {
                index % 8 == 0 -> 12.dp.toPx()
                index % 4 == 0 -> 8.dp.toPx()
                else -> 4.dp.toPx()
            }
            drawLine(tick, Offset(x, size.height), Offset(x, size.height - length))
        }
        drawLine(tick, Offset(0f, size.height), Offset(size.width, size.height))
    }
}
