package lmeditor.text

import java.math.BigDecimal
import java.math.RoundingMode

/**
 * Round [value] to [digits] decimal places the way Python's `round` does:
 * the exact binary value of the double, rounded half to even.
 *
 * The rounding is a rule, and a rule is written once. A lineage confidence, a
 * citation precision and a claim-lineage confidence are all rounded the same
 * way here, so they cannot drift apart: `kotlin.math.round` rounds half *up*,
 * which is a different rule, and a number that is nearly the same is not the
 * same number.
 */
internal fun roundTo(value: Double, digits: Int): Double =
    BigDecimal(value).setScale(digits, RoundingMode.HALF_EVEN).toDouble()
