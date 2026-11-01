package lmeditor.desktop.ui

import androidx.compose.ui.graphics.Color
import kotlin.math.max
import kotlin.math.min
import kotlin.test.Test
import kotlin.test.assertTrue
import lmeditor.core.EntailmentStatus

/**
 * The contrast of the verdict colours.
 *
 * A verdict is read as text on the chip that carries it, so the colour has to
 * clear the 4.5:1 of body text against the theme's own surface, and against
 * the tint the chip itself is drawn in.
 */
class VerdictContrastTest {

    @Test
    fun `every verdict clears the text contrast in the paper theme`() {
        assertVerdictsClear(LightVerdictPalette, LightColors.surface)
    }

    @Test
    fun `every verdict clears the text contrast in the dark theme`() {
        assertVerdictsClear(DarkVerdictPalette, DarkColors.surface)
    }
}

/** Every verdict of a palette has to be legible against the surface it is drawn on. */
private fun assertVerdictsClear(palette: VerdictPalette, surface: Color) {
    for (status in EntailmentStatus.entries) {
        val color = palette.of(status)
        val chip = blend(color, surface, CHIP_TINT_ALPHA)
        assertTrue(
            contrast(color, surface) >= 4.5,
            "$status reads ${contrast(color, surface)} against the paper theme's surface",
        )
        assertTrue(
            contrast(color, chip) >= 4.5,
            "$status reads ${contrast(color, chip)} on its own chip in the paper theme",
        )
    }
}

/** The colour a tint of one colour over another is drawn as. */
private fun blend(over: Color, under: Color, alpha: Float): Color = Color(
    red = over.red * alpha + under.red * (1f - alpha),
    green = over.green * alpha + under.green * (1f - alpha),
    blue = over.blue * alpha + under.blue * (1f - alpha),
)

/** The WCAG contrast ratio between two colours. */
private fun contrast(first: Color, second: Color): Double {
    val a = luminance(first)
    val b = luminance(second)
    return (max(a, b) + 0.05) / (min(a, b) + 0.05)
}

/** The relative luminance of a colour. */
private fun luminance(color: Color): Double = 0.2126 * channel(color.red) +
    0.7152 * channel(color.green) +
    0.0722 * channel(color.blue)

/** One channel of a colour, linearised as WCAG asks. */
private fun channel(value: Float): Double {
    val c = value.toDouble()
    return if (c <= 0.03928) c / 12.92 else Math.pow((c + 0.055) / 1.055, 2.4)
}
