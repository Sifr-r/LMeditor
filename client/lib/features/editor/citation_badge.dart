import 'package:flutter/material.dart';
import '../../core/models/citation.dart';
import '../../core/theme.dart';

/// Interactive inline citation badge widget designed to be embedded in [WidgetSpan].
///
/// Displays `[#chunkId]` with entailment-coded styling:
/// - Verified ([NLILabel.entailment]): Emerald green border/bg (#10B981).
/// - Contradiction / Ungrounded ([NLILabel.contradiction] or `ungrounded`): Crimson red (#EF4444).
/// - Neutral ([NLILabel.neutral]): Amber (#F59E0B).
///
/// Features rich tooltip, micro-interactions on mouse hover, and an [onTap] callback.
class CitationBadge extends StatefulWidget {
  final String chunkId;
  final String? citationId;
  final NLILabel nliLabel;
  final double confidence;
  final VoidCallback? onTap;
  final bool isSelected;

  const CitationBadge({
    super.key,
    required this.chunkId,
    this.citationId,
    this.nliLabel = NLILabel.neutral,
    this.confidence = 0.95,
    this.onTap,
    this.isSelected = false,
  });

  @override
  State<CitationBadge> createState() => _CitationBadgeState();
}

class _CitationBadgeState extends State<CitationBadge> {
  bool _isHovered = false;

  Color get _entailmentColor {
    switch (widget.nliLabel) {
      case NLILabel.entailment:
        return StudioTheme.verifiedEmerald;
      case NLILabel.contradiction:
        return StudioTheme.contradictionCrimson;
      case NLILabel.ungrounded:
        return StudioTheme.ungroundedCrimson;
      case NLILabel.neutral:
        return StudioTheme.neutralAmber;
    }
  }

  @override
  Widget build(BuildContext context) {
    final baseColor = _entailmentColor;
    final isSelected = widget.isSelected;
    final isHovered = _isHovered;

    final bgColor = isHovered || isSelected
        ? baseColor.withValues(alpha: 0.28)
        : baseColor.withValues(alpha: 0.14);

    final borderColor = isHovered || isSelected
        ? baseColor.withValues(alpha: 0.95)
        : baseColor.withValues(alpha: 0.50);

    final percentString = (widget.confidence * 100).toStringAsFixed(1);
    final tooltipMessage =
        'Chunk [#${widget.chunkId}]\n'
        'Entailment: ${widget.nliLabel.displayName}\n'
        'Confidence: $percentString%\n'
        'Click to inspect claim lineage in Verification Panel';

    return MouseRegion(
      cursor: SystemMouseCursors.click,
      onEnter: (_) => setState(() => _isHovered = true),
      onExit: (_) => setState(() => _isHovered = false),
      child: Tooltip(
        message: tooltipMessage,
        waitDuration: const Duration(milliseconds: 150),
        child: GestureDetector(
          onTap: widget.onTap,
          child: AnimatedContainer(
            duration: const Duration(milliseconds: 180),
            curve: Curves.easeOutCubic,
            margin: const EdgeInsets.symmetric(horizontal: 2.5),
            padding: const EdgeInsets.symmetric(horizontal: 5.5, vertical: 1.5),
            decoration: BoxDecoration(
              color: bgColor,
              borderRadius: BorderRadius.circular(4.5),
              border: Border.all(
                color: borderColor,
                width: isHovered || isSelected ? 1.4 : 1.0,
              ),
              boxShadow: isHovered || isSelected
                  ? [
                      BoxShadow(
                        color: baseColor.withValues(alpha: 0.35),
                        blurRadius: 7,
                        spreadRadius: 0.5,
                        offset: const Offset(0, 1),
                      ),
                    ]
                  : null,
            ),
            child: Row(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.center,
              children: [
                Icon(
                  widget.nliLabel.icon,
                  size: 10.5,
                  color: baseColor,
                ),
                const SizedBox(width: 3.5),
                Text(
                  '[#${widget.chunkId}]',
                  style: TextStyle(
                    fontFamily: 'monospace',
                    fontSize: 11.0,
                    fontWeight: FontWeight.w700,
                    letterSpacing: 0.3,
                    color: baseColor,
                    height: 1.1,
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
