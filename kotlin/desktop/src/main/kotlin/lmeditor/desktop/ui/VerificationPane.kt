package lmeditor.desktop.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import lmeditor.core.Claim
import lmeditor.core.EntailmentStatus
import lmeditor.desktop.model.StudioController
import lmeditor.desktop.model.StudioState

/**
 * Which claims the verifier's report shows.
 *
 * The report of a long document is mostly claims that rest on evidence, and
 * the ones that do not are what the operator is looking for.
 */
internal enum class ClaimFilter(val wire: String) {
    ALL("all"),
    VERIFIED("verified"),
    FLAGGED("flagged"),
}

/**
 * The verifier's report on the manuscript.
 *
 * Every claim the run measured is listed with the verdict it reached, and
 * selecting one shows the reasoning and the passages it rests on. The
 * scorecard at the top is the document-level aggregate.
 */
@Composable
fun VerificationPane(
    state: StudioState,
    controller: StudioController,
    modifier: Modifier = Modifier,
) {
    var filterName by rememberSaveable { mutableStateOf(ClaimFilter.ALL.wire) }
    val filter = ClaimFilter.entries.firstOrNull { it.wire == filterName } ?: ClaimFilter.ALL
    Column(
        modifier = modifier
            .fillMaxSize()
            .padding(12.dp)
            .verticalScroll(rememberScrollState()),
    ) {
        Text("Grounding", style = MaterialTheme.typography.titleMedium)
        val card = state.scorecard
        if (card == null) {
            Text(
                "No scorecard yet.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(top = 4.dp),
            )
        } else {
            Row(
                modifier = Modifier.fillMaxWidth().padding(top = 6.dp),
                horizontalArrangement = Arrangement.spacedBy(14.dp),
            ) {
                Metric("F-0.5", "%.2f".format(card.fBeta))
                Metric("Precision", "%.2f".format(card.precision))
                Metric("Recall", "%.2f".format(card.recall))
            }
            Row(
                modifier = Modifier.fillMaxWidth().padding(top = 4.dp),
                horizontalArrangement = Arrangement.spacedBy(14.dp),
            ) {
                Metric("Supported", card.supportedClaims.toString(), EntailmentStatus.ENTAILED)
                Metric("Ungrounded", card.ungroundedClaims.toString(), EntailmentStatus.UNGROUNDED)
                Metric("Contradicted", card.contradictedClaims.toString(), EntailmentStatus.CONTRADICTED)
                Metric("Neutral", card.neutralClaims.toString(), EntailmentStatus.NEUTRAL)
            }
        }

        HorizontalDivider(modifier = Modifier.padding(vertical = 10.dp))

        Text("Claims (${state.claims.size})", style = MaterialTheme.typography.titleMedium)
        Row(
            modifier = Modifier.fillMaxWidth().padding(top = 6.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            for (choice in ClaimFilter.entries) {
                FilterChip(
                    selected = choice == filter,
                    onClick = { filterName = choice.wire },
                    label = { Text(filterLabel(choice)) },
                )
            }
        }
        val shown = state.claims.filter { claim -> filter.keeps(claim) }
        if (shown.isEmpty()) {
            Text(
                if (state.claims.isEmpty()) {
                    "No claims have been verified yet."
                } else {
                    "No claims match this filter."
                },
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(top = 4.dp),
            )
        }
        for (claim in shown) {
            ClaimRow(
                claim = claim,
                selected = claim.claimId == state.selectedClaimId,
                onSelect = { controller.selectClaim(claim.claimId) },
            )
        }

        val selected = state.claims.firstOrNull { it.claimId == state.selectedClaimId }
        if (selected != null) {
            HorizontalDivider(modifier = Modifier.padding(vertical = 10.dp))
            ClaimDetail(selected, state, controller)
        }

        val chunk = state.chunks.firstOrNull { it.chunkId == state.selectedChunkId }
        if (chunk != null) {
            HorizontalDivider(modifier = Modifier.padding(vertical = 10.dp))
            Text("The passage", style = MaterialTheme.typography.titleMedium)
            Text(
                chunk.text,
                style = MaterialTheme.typography.bodyMedium.copy(fontFamily = FontFamily.Serif),
                modifier = Modifier.padding(top = 4.dp),
            )
            Text(
                "chunk ${chunk.chunkId.take(12)}…",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(top = 4.dp),
            )
        }
    }
}

/** One measured claim. */
@Composable
private fun ClaimRow(
    claim: Claim,
    selected: Boolean,
    onSelect: () -> Unit,
    modifier: Modifier = Modifier,
) {
    Card(
        colors = CardDefaults.cardColors(
            containerColor = if (selected) {
                MaterialTheme.colorScheme.primary.copy(alpha = 0.12f)
            } else {
                MaterialTheme.colorScheme.surface
            },
        ),
        modifier = modifier
            .fillMaxWidth()
            .padding(top = 6.dp)
            // The row says whether it is the selected one, so the selection is
            // a state the window reports and not only a tint on the card.
            .selectable(selected = selected, onClick = onSelect),
    ) {
        Row(
            modifier = Modifier.padding(10.dp),
            verticalAlignment = Alignment.Top,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            VerdictSwatch(colorFor(claim.entailmentStatus))
            Column(modifier = Modifier.weight(1f)) {
                Text(claim.text, style = MaterialTheme.typography.bodyMedium)
                Text(
                    "${claim.entailmentStatus.wire} · %.2f".format(claim.entailmentScore) +
                        if (claim.citedChunkIds.isEmpty()) " · uncited" else "",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
    }
}

/** The reasoning and the passages behind one claim. */
@Composable
private fun ClaimDetail(
    claim: Claim,
    state: StudioState,
    controller: StudioController,
    modifier: Modifier = Modifier,
) {
    Column(modifier = modifier.fillMaxWidth()) {
        Text("Why", style = MaterialTheme.typography.titleMedium)
        Text(
            claim.reasoning?.takeIf { it.isNotBlank() } ?: "The verifier gave no reason.",
            style = MaterialTheme.typography.bodyMedium,
            modifier = Modifier.padding(top = 4.dp),
        )
        val premise = claim.premiseText.orEmpty()
        if (premise.isNotBlank()) {
            Text("Premise", style = MaterialTheme.typography.titleMedium, modifier = Modifier.padding(top = 8.dp))
            Text(
                premise,
                style = MaterialTheme.typography.bodySmall.copy(fontFamily = FontFamily.Serif),
                modifier = Modifier.padding(top = 2.dp),
            )
        }
        if (claim.citedChunkIds.isNotEmpty()) {
            Text("Cites", style = MaterialTheme.typography.titleMedium, modifier = Modifier.padding(top = 8.dp))
            for (chunkId in claim.citedChunkIds) {
                Text(
                    chunkId.take(16) + "…",
                    style = MaterialTheme.typography.bodySmall.copy(fontFamily = FontFamily.Monospace),
                    color = MaterialTheme.colorScheme.primary,
                    modifier = Modifier
                        .clickable { controller.selectChunk(chunkId) }
                        .padding(top = 2.dp),
                )
            }
        }
    }
}

/** One number of the scorecard. */
@Composable
private fun Metric(
    label: String,
    value: String,
    status: EntailmentStatus? = null,
    modifier: Modifier = Modifier,
) {
    Column(modifier = modifier) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(4.dp)) {
            if (status != null) {
                VerdictSwatch(colorFor(status))
            }
            Text(label, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        Text(value, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
    }
}

/** The colour a verdict is drawn in, taken from the theme in force. */
@Composable
internal fun colorFor(status: EntailmentStatus): Color = verdictPalette().of(status)

/** What a filter shows, in words. */
private fun filterLabel(filter: ClaimFilter): String = when (filter) {
    ClaimFilter.ALL -> "All"
    ClaimFilter.VERIFIED -> "Verified"
    ClaimFilter.FLAGGED -> "Flagged"
}

/** Whether a claim is shown under a filter. */
private fun ClaimFilter.keeps(claim: Claim): Boolean = when (this) {
    ClaimFilter.ALL -> true
    ClaimFilter.VERIFIED -> claim.entailmentStatus == EntailmentStatus.ENTAILED
    ClaimFilter.FLAGGED -> claim.entailmentStatus == EntailmentStatus.CONTRADICTED ||
        claim.entailmentStatus == EntailmentStatus.UNGROUNDED
}
