@file:OptIn(androidx.compose.ui.text.ExperimentalTextApi::class)

package lmeditor.desktop.ui

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.sp
import lmeditor.core.EntailmentStatus

/**
 * The studio's colours and type.
 *
 * The manuscript is read for a long time, so the body type is a serif at a
 * comfortable measure, and the chrome around it is a sans at a smaller size.
 * A citation chip is the one thing that must never be missed, so it gets the
 * strongest colour in the scheme.
 */
private val Paper = Color(0xFFECEDE9)
private val Ink = Color(0xFF252C29)
private val Evergreen = Color(0xFF24594C)
private val Amber = Color(0xFFB26A00)
private val Moss = Color(0xFF3F6B45)
private val Rust = Color(0xFF9B3A26)

// Resolve named desktop faces locally, keeping the UI usable offline.
private val ManuscriptTypeface = FontFamily("Georgia")
private val ChromeTypeface = FontFamily("Candara")

internal val LightColors = lightColorScheme(
    primary = Evergreen,
    onPrimary = Color.White,
    primaryContainer = Color(0xFFDCEAE3),
    onPrimaryContainer = Color(0xFF173E32),
    secondary = Moss,
    onSecondary = Color.White,
    tertiary = Amber,
    onTertiary = Color.White,
    error = Rust,
    onError = Color.White,
    background = Paper,
    onBackground = Ink,
    surface = Color(0xFFFAFAF7),
    onSurface = Ink,
    surfaceVariant = Color(0xFFE6E9E3),
    onSurfaceVariant = Color(0xFF5C655F),
    outline = Color(0xFF89948B),
    outlineVariant = Color(0xFFD6DBD4),
)

internal val DarkColors = darkColorScheme(
    primary = Color(0xFFA4CDB9),
    onPrimary = Color(0xFF12382C),
    primaryContainer = Color(0xFF284D3F),
    onPrimaryContainer = Color(0xFFDCEAE3),
    secondary = Color(0xFF9CCBA3),
    onSecondary = Color(0xFF11301A),
    tertiary = Color(0xFFF0B061),
    onTertiary = Color(0xFF3A2405),
    error = Color(0xFFF2B8AE),
    onError = Color(0xFF4A1509),
    background = Color(0xFF171C19),
    onBackground = Color(0xFFEDE8DE),
    surface = Color(0xFF222924),
    onSurface = Color(0xFFEDE8DE),
    surfaceVariant = Color(0xFF303B33),
    onSurfaceVariant = Color(0xFFBAC7BD),
    outline = Color(0xFF7D8C80),
    outlineVariant = Color(0xFF3E4A41),
)

/**
 * The colours a verdict is drawn in.
 *
 * Each theme carries its own set: the paper theme reads a verdict against
 * near-white paper and the dark theme against near-black paper, so the same
 * hue is drawn dark in one and light in the other. Every value clears 4.5:1
 * against the surface of its own theme, so a verdict is legible as text and
 * not only as a tint.
 */
internal data class VerdictPalette(
    val entailed: Color,
    val contradicted: Color,
    val neutral: Color,
    val ungrounded: Color,
    val unknown: Color,
) {
    /** The colour one verdict is drawn in. */
    fun of(status: EntailmentStatus?): Color = when (status) {
        EntailmentStatus.ENTAILED -> entailed
        EntailmentStatus.CONTRADICTED -> contradicted
        EntailmentStatus.NEUTRAL -> neutral
        EntailmentStatus.UNGROUNDED -> ungrounded
        null -> unknown
    }
}

/** Amber, darkened until it can be read as text on the paper theme. */
private val AmberDeep = Color(0xFF8A5200)

/** The verdicts of the paper theme. */
internal val LightVerdictPalette = VerdictPalette(
    entailed = Evergreen,
    contradicted = Rust,
    neutral = Color(0xFF5C655F),
    ungrounded = AmberDeep,
    unknown = Color(0xFF3B5B92),
)

/** The verdicts of the dark theme. */
internal val DarkVerdictPalette = VerdictPalette(
    entailed = Color(0xFFA4CDB9),
    contradicted = Color(0xFFF2B8AE),
    neutral = Color(0xFFBAC7BD),
    ungrounded = Color(0xFFF0B061),
    unknown = Color(0xFF9CCBA3),
)

/** The verdict colours of the theme in force. */
internal val LocalVerdictPalette = staticCompositionLocalOf { LightVerdictPalette }

/** The verdict colours of the theme in force. */
@Composable
internal fun verdictPalette(): VerdictPalette = LocalVerdictPalette.current

private val StudioTypography = Typography(
    headlineMedium = TextStyle(
        fontFamily = ManuscriptTypeface,
        fontWeight = FontWeight.SemiBold,
        fontSize = 26.sp,
        lineHeight = 32.sp,
    ),
    titleLarge = TextStyle(
        fontFamily = ManuscriptTypeface,
        fontWeight = FontWeight.SemiBold,
        fontSize = 19.sp,
        lineHeight = 25.sp,
    ),
    titleMedium = TextStyle(
        fontFamily = ChromeTypeface,
        fontWeight = FontWeight.SemiBold,
        fontSize = 14.sp,
        lineHeight = 20.sp,
    ),
    bodyLarge = TextStyle(
        fontFamily = ManuscriptTypeface,
        fontSize = 16.sp,
        lineHeight = 26.sp,
    ),
    bodyMedium = TextStyle(
        fontFamily = ChromeTypeface,
        fontSize = 13.sp,
        lineHeight = 19.sp,
    ),
    bodySmall = TextStyle(
        fontFamily = ChromeTypeface,
        fontSize = 11.sp,
        lineHeight = 16.sp,
    ),
    labelMedium = TextStyle(
        fontFamily = ChromeTypeface,
        fontWeight = FontWeight.Medium,
        fontSize = 12.sp,
        lineHeight = 16.sp,
    ),
)

/** The theme every screen of the client uses. */
@Composable
fun LMeditorTheme(content: @Composable () -> Unit) {
    val dark = isSystemInDarkTheme()
    MaterialTheme(
        colorScheme = if (dark) DarkColors else LightColors,
        typography = StudioTypography,
        content = {
            CompositionLocalProvider(
                LocalVerdictPalette provides if (dark) DarkVerdictPalette else LightVerdictPalette,
            ) {
                content()
            }
        },
    )
}
