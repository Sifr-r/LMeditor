import 'package:flutter/material.dart';
import '../../core/models/claim.dart';
import '../../core/models/chunk.dart';
import '../../core/models/citation.dart';
import '../../core/theme.dart';
import 'source_chunk_viewer.dart';

enum ClaimFilter { all, verified, flagged }

/// Claim-by-claim NLI scorecard and source chunk inspector.
///
/// Features:
/// - Summary bar: Total Claims, Entailment Rate (%), Contradictions, Ungrounded.
/// - Filter chips (All, Verified, Flagged/Ungrounded).
/// - List of claims with NLI score indicator (linear progress bar colored by label).
/// - Selected claim detailed card:
///   - Proposition statement.
///   - Full sentence context.
///   - NLI model explanation and confidence score.
/// - Ground Truth Passage Inspector:
///   - Displays premise [SourceChunk] content.
///   - Highlights character offsets corresponding to [CitationLineage].
///   - Displays source document metadata and SHA-256 hash.
class VerificationPanel extends StatefulWidget {
  final List<Claim> claims;
  final Map<String, SourceChunk> chunks;
  final String? selectedClaimId;
  final ValueChanged<Claim>? onSelectClaim;

  const VerificationPanel({
    super.key,
    required this.claims,
    required this.chunks,
    this.selectedClaimId,
    this.onSelectClaim,
  });

  @override
  State<VerificationPanel> createState() => _VerificationPanelState();
}

class _VerificationPanelState extends State<VerificationPanel> {
  ClaimFilter _activeFilter = ClaimFilter.all;

  Claim? get _selectedClaim {
    if (widget.selectedClaimId != null) {
      for (final c in widget.claims) {
        if (c.claimId == widget.selectedClaimId) return c;
      }
    }
    return widget.claims.isNotEmpty ? widget.claims.first : null;
  }

  @override
  Widget build(BuildContext context) {
    final totalClaims = widget.claims.length;
    final verifiedCount = widget.claims
        .where((c) => c.nliLabel == NLILabel.entailment)
        .length;
    final contradictionCount = widget.claims
        .where((c) => c.nliLabel == NLILabel.contradiction)
        .length;
    final ungroundedCount = widget.claims
        .where((c) => c.nliLabel == NLILabel.ungrounded)
        .length;

    final entailmentRate = totalClaims > 0
        ? (verifiedCount / totalClaims * 100).toStringAsFixed(1)
        : '100.0';

    final filteredClaims = widget.claims.where((c) {
      switch (_activeFilter) {
        case ClaimFilter.all:
          return true;
        case ClaimFilter.verified:
          return c.nliLabel == NLILabel.entailment;
        case ClaimFilter.flagged:
          return c.nliLabel == NLILabel.contradiction ||
              c.nliLabel == NLILabel.ungrounded ||
              c.nliLabel == NLILabel.neutral;
      }
    }).toList();

    final activeClaim = _selectedClaim;
    final activeChunk = activeClaim != null ? widget.chunks[activeClaim.chunkId] : null;

    CitationLineage? activeLineage;
    if (activeClaim != null) {
      activeLineage = CitationLineage(
        citationId: activeClaim.citationId,
        sourceChunkId: activeClaim.chunkId,
        exactSpan: activeClaim.proposition,
        startOffset: activeClaim.spanStart ?? 0,
        endOffset: activeClaim.spanEnd ?? activeClaim.proposition.length,
        confidenceScore: activeClaim.confidence,
      );
    }

    return Container(
      color: StudioTheme.background,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          _buildPanelHeader(),
          _buildSummaryBar(
            total: totalClaims,
            entailmentRate: entailmentRate,
            contradictions: contradictionCount,
            ungrounded: ungroundedCount,
          ),
          _buildFilterChips(),
          Expanded(
            flex: 5,
            child: filteredClaims.isEmpty
                ? _buildEmptyClaims()
                : ListView.builder(
                    padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
                    itemCount: filteredClaims.length,
                    itemBuilder: (context, index) {
                      final claim = filteredClaims[index];
                      final isSelected = activeClaim?.claimId == claim.claimId;
                      return _buildClaimItem(claim, isSelected);
                    },
                  ),
          ),
          if (activeClaim != null) ...[
            const Divider(color: StudioTheme.borderSubtle, height: 1),
            _buildSelectedClaimCard(activeClaim),
          ],
          Expanded(
            flex: 4,
            child: SourceChunkViewer(
              chunk: activeChunk,
              lineage: activeLineage,
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildPanelHeader() {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
      decoration: const BoxDecoration(
        color: StudioTheme.surfacePane,
        border: Border(bottom: BorderSide(color: StudioTheme.borderSubtle)),
      ),
      child: const Row(
        children: [
          Icon(Icons.fact_check_outlined, size: 17, color: StudioTheme.verifiedEmerald),
          SizedBox(width: 8),
          Expanded(
            child: Text(
              'NLI Verification Scorecard',
              style: TextStyle(
                fontSize: 13.5,
                fontWeight: FontWeight.w700,
                color: StudioTheme.textPrimary,
                letterSpacing: 0.2,
              ),
              overflow: TextOverflow.ellipsis,
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildSummaryBar({
    required int total,
    required String entailmentRate,
    required int contradictions,
    required int ungrounded,
  }) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
      color: const Color(0xFF0E1524),
      child: Row(
        children: [
          _buildStatPill('Total Claims', '$total', StudioTheme.textPrimary),
          const SizedBox(width: 6),
          _buildStatPill(
            'Entailment',
            '$entailmentRate%',
            double.tryParse(entailmentRate) != null && double.parse(entailmentRate) >= 85
                ? StudioTheme.verifiedEmerald
                : StudioTheme.neutralAmber,
          ),
          const SizedBox(width: 6),
          _buildStatPill(
            'Contradict',
            '$contradictions',
            contradictions > 0 ? StudioTheme.contradictionCrimson : StudioTheme.textMuted,
          ),
          const SizedBox(width: 6),
          _buildStatPill(
            'Ungrounded',
            '$ungrounded',
            ungrounded > 0 ? StudioTheme.ungroundedCrimson : StudioTheme.textMuted,
          ),
        ],
      ),
    );
  }

  Widget _buildStatPill(String label, String value, Color valueColor) {
    return Expanded(
      child: Container(
        padding: const EdgeInsets.symmetric(vertical: 5, horizontal: 4),
        decoration: BoxDecoration(
          color: StudioTheme.surfaceCard,
          borderRadius: BorderRadius.circular(5),
          border: Border.all(color: StudioTheme.borderSubtle),
        ),
        child: Column(
          children: [
            Text(
              label,
              style: const TextStyle(
                fontSize: 9.5,
                color: StudioTheme.textMuted,
                fontWeight: FontWeight.w500,
              ),
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
            ),
            const SizedBox(height: 2),
            Text(
              value,
              style: TextStyle(
                fontFamily: 'monospace',
                fontSize: 12.0,
                fontWeight: FontWeight.w700,
                color: valueColor,
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildFilterChips() {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
      decoration: const BoxDecoration(
        color: StudioTheme.surfacePane,
        border: Border(bottom: BorderSide(color: StudioTheme.borderSubtle)),
      ),
      child: SingleChildScrollView(
        scrollDirection: Axis.horizontal,
        child: Row(
          children: [
            _buildFilterChip('All', ClaimFilter.all),
            const SizedBox(width: 6),
            _buildFilterChip('Verified', ClaimFilter.verified),
            const SizedBox(width: 6),
            _buildFilterChip('Flagged', ClaimFilter.flagged),
          ],
        ),
      ),
    );
  }

  Widget _buildFilterChip(String label, ClaimFilter filter) {
    final isSelected = _activeFilter == filter;
    return InkWell(
      onTap: () => setState(() => _activeFilter = filter),
      borderRadius: BorderRadius.circular(4),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3.5),
        decoration: BoxDecoration(
          color: isSelected ? StudioTheme.primary.withValues(alpha: 0.25) : Colors.transparent,
          borderRadius: BorderRadius.circular(4),
          border: Border.all(
            color: isSelected ? StudioTheme.primaryLight : StudioTheme.borderSubtle,
          ),
        ),
        child: Text(
          label,
          style: TextStyle(
            fontSize: 11,
            fontWeight: isSelected ? FontWeight.w600 : FontWeight.w400,
            color: isSelected ? Colors.white : StudioTheme.textSecondary,
          ),
        ),
      ),
    );
  }

  Widget _buildEmptyClaims() {
    return const Center(
      child: Padding(
        padding: EdgeInsets.all(16),
        child: Text(
          'No claims found for this filter.',
          style: TextStyle(fontSize: 12, color: StudioTheme.textMuted),
        ),
      ),
    );
  }

  Widget _buildClaimItem(Claim claim, bool isSelected) {
    final nliColor = claim.nliLabel.color;
    final percent = (claim.confidence * 100).toStringAsFixed(1);

    return Container(
      margin: const EdgeInsets.only(bottom: 6),
      decoration: BoxDecoration(
        color: isSelected
            ? StudioTheme.surfaceCard
            : const Color(0xFF101726),
        borderRadius: BorderRadius.circular(6),
        border: Border.all(
          color: isSelected ? StudioTheme.primaryLight : StudioTheme.borderSubtle,
          width: isSelected ? 1.2 : 1.0,
        ),
      ),
      child: Material(
        color: Colors.transparent,
        child: InkWell(
          onTap: () => widget.onSelectClaim?.call(claim),
          borderRadius: BorderRadius.circular(6),
          child: Padding(
            padding: const EdgeInsets.all(9),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                // Proposition
                Text(
                  claim.proposition,
                  style: TextStyle(
                    fontSize: 12.0,
                    fontWeight: isSelected ? FontWeight.w600 : FontWeight.w500,
                    color: isSelected ? Colors.white : StudioTheme.textPrimary,
                    height: 1.35,
                  ),
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                ),
                const SizedBox(height: 6),
                // Badge Row
                Row(
                  children: [
                    Icon(claim.nliLabel.icon, size: 12, color: nliColor),
                    const SizedBox(width: 4),
                    Text(
                      claim.nliLabel.displayName,
                      style: TextStyle(
                        fontSize: 10.5,
                        fontWeight: FontWeight.w600,
                        color: nliColor,
                      ),
                    ),
                    const SizedBox(width: 8),
                    Container(
                      padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 1),
                      decoration: BoxDecoration(
                        color: StudioTheme.surfacePane,
                        borderRadius: BorderRadius.circular(3),
                      ),
                      child: Text(
                        '[#${claim.chunkId}]',
                        style: const TextStyle(
                          fontFamily: 'monospace',
                          fontSize: 9.5,
                          color: StudioTheme.textSecondary,
                        ),
                      ),
                    ),
                    const Spacer(),
                    Text(
                      '$percent%',
                      style: TextStyle(
                        fontFamily: 'monospace',
                        fontSize: 10.5,
                        fontWeight: FontWeight.w700,
                        color: nliColor,
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 5),
                // Linear Progress bar colored by NLI label
                ClipRRect(
                  borderRadius: BorderRadius.circular(2),
                  child: LinearProgressIndicator(
                    value: claim.confidence.clamp(0.0, 1.0),
                    minHeight: 3.0,
                    backgroundColor: StudioTheme.surfacePane,
                    valueColor: AlwaysStoppedAnimation<Color>(nliColor),
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }

  Widget _buildSelectedClaimCard(Claim claim) {
    final nliColor = claim.nliLabel.color;

    return Container(
      padding: const EdgeInsets.all(12),
      decoration: const BoxDecoration(
        color: Color(0xFF131C2D),
        border: Border(
          bottom: BorderSide(color: StudioTheme.borderSubtle),
        ),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: [
          Row(
            children: [
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                decoration: BoxDecoration(
                  color: nliColor.withValues(alpha: 0.15),
                  borderRadius: BorderRadius.circular(4),
                  border: Border.all(color: nliColor.withValues(alpha: 0.6)),
                ),
                child: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Icon(claim.nliLabel.icon, size: 12, color: nliColor),
                    const SizedBox(width: 4),
                    Text(
                      claim.nliLabel.displayName.toUpperCase(),
                      style: TextStyle(
                        fontFamily: 'monospace',
                        fontSize: 10,
                        fontWeight: FontWeight.w800,
                        color: nliColor,
                      ),
                    ),
                  ],
                ),
              ),
              const SizedBox(width: 8),
              Text(
                'Confidence: ${(claim.confidence * 100).toStringAsFixed(1)}%',
                style: TextStyle(
                  fontFamily: 'monospace',
                  fontSize: 11,
                  fontWeight: FontWeight.w700,
                  color: nliColor,
                ),
              ),
            ],
          ),
          const SizedBox(height: 8),
          const Text(
            'PROPOSITION STATEMENT:',
            style: TextStyle(
              fontFamily: 'monospace',
              fontSize: 9.5,
              fontWeight: FontWeight.w700,
              color: StudioTheme.textMuted,
              letterSpacing: 0.5,
            ),
          ),
          const SizedBox(height: 3),
          SelectableText(
            '"${claim.proposition}"',
            style: const TextStyle(
              fontSize: 12.0,
              fontStyle: FontStyle.italic,
              color: Color(0xFFE2E8F0),
              height: 1.35,
            ),
          ),
          const SizedBox(height: 6),
          const Text(
            'SENTENCE CONTEXT:',
            style: TextStyle(
              fontFamily: 'monospace',
              fontSize: 9.5,
              fontWeight: FontWeight.w700,
              color: StudioTheme.textMuted,
              letterSpacing: 0.5,
            ),
          ),
          const SizedBox(height: 3),
          SelectableText(
            claim.fullSentence,
            style: const TextStyle(
              fontSize: 11.5,
              color: StudioTheme.textSecondary,
              height: 1.35,
            ),
          ),
          if (claim.explanation != null && claim.explanation!.isNotEmpty) ...[
            const SizedBox(height: 6),
            const Text(
              'NLI MODEL RATIONALE:',
              style: TextStyle(
                fontFamily: 'monospace',
                fontSize: 9.5,
                fontWeight: FontWeight.w700,
                color: StudioTheme.textMuted,
                letterSpacing: 0.5,
              ),
            ),
            const SizedBox(height: 3),
            Text(
              claim.explanation!,
              style: const TextStyle(
                fontSize: 11.0,
                color: Color(0xFF93C5FD),
                height: 1.3,
              ),
            ),
          ],
        ],
      ),
    );
  }
}
