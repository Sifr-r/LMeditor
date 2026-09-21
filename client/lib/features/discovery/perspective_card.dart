import 'package:flutter/material.dart';
import '../../core/models/perspective.dart';
import '../../core/theme.dart';

/// Visual card rendering discovered STORM personas:
/// - Avatar with role badge/icon.
/// - Persona name and role (e.g., "Domain Historian", "Critical Empirical Skeptic").
/// - Description of perspective.
/// - Expandable focus research questions.
/// - Active toggle / selection state.
class PerspectiveCard extends StatefulWidget {
  final StormPerspective perspective;
  final ValueChanged<bool>? onToggleSelected;
  final VoidCallback? onTap;

  const PerspectiveCard({
    super.key,
    required this.perspective,
    this.onToggleSelected,
    this.onTap,
  });

  @override
  State<PerspectiveCard> createState() => _PerspectiveCardState();
}

class _PerspectiveCardState extends State<PerspectiveCard> {
  bool _isExpanded = false;
  bool _isHovered = false;

  @override
  Widget build(BuildContext context) {
    final perspective = widget.perspective;
    final isSelected = perspective.isSelected;

    final borderColor = isSelected
        ? (_isHovered ? StudioTheme.primaryLight : StudioTheme.primary.withValues(alpha: 0.6))
        : (_isHovered ? StudioTheme.border : StudioTheme.borderSubtle);

    final bgColor = isSelected
        ? (_isHovered ? const Color(0xFF1B243B) : const Color(0xFF151D30))
        : (_isHovered ? StudioTheme.surfaceHover : StudioTheme.surfaceCard);

    return MouseRegion(
      onEnter: (_) => setState(() => _isHovered = true),
      onExit: (_) => setState(() => _isHovered = false),
      child: AnimatedContainer(
        duration: const Duration(milliseconds: 160),
        margin: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
        decoration: BoxDecoration(
          color: bgColor,
          borderRadius: BorderRadius.circular(8),
          border: Border.all(color: borderColor, width: isSelected ? 1.2 : 1.0),
          boxShadow: isSelected && _isHovered
              ? [
                  BoxShadow(
                    color: StudioTheme.primary.withValues(alpha: 0.2),
                    blurRadius: 8,
                    offset: const Offset(0, 2),
                  ),
                ]
              : null,
        ),
        child: InkWell(
          onTap: widget.onTap ?? () => widget.onToggleSelected?.call(!isSelected),
          borderRadius: BorderRadius.circular(8),
          child: Padding(
            padding: const EdgeInsets.all(12),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                // Top Row: Avatar, Name/Role, Toggle
                Row(
                  crossAxisAlignment: CrossAxisAlignment.center,
                  children: [
                    // Avatar with role symbol
                    Container(
                      width: 36,
                      height: 36,
                      decoration: BoxDecoration(
                        color: isSelected
                            ? StudioTheme.primary.withValues(alpha: 0.25)
                            : StudioTheme.surfacePane,
                        shape: BoxShape.circle,
                        border: Border.all(
                          color: isSelected ? StudioTheme.primaryLight : StudioTheme.border,
                        ),
                      ),
                      alignment: Alignment.center,
                      child: Text(
                        perspective.avatarSymbol,
                        style: const TextStyle(fontSize: 18),
                      ),
                    ),
                    const SizedBox(width: 10),
                    // Name & Role
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(
                            perspective.name,
                            style: const TextStyle(
                              fontSize: 13.5,
                              fontWeight: FontWeight.w700,
                              color: StudioTheme.textPrimary,
                            ),
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                          ),
                          const SizedBox(height: 2),
                          Container(
                            padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 1.5),
                            decoration: BoxDecoration(
                              color: StudioTheme.accentCyan.withValues(alpha: 0.12),
                              borderRadius: BorderRadius.circular(4),
                              border: Border.all(
                                color: StudioTheme.accentCyan.withValues(alpha: 0.3),
                                width: 0.8,
                              ),
                            ),
                            child: Text(
                              perspective.role,
                              style: const TextStyle(
                                fontFamily: 'monospace',
                                fontSize: 10.5,
                                fontWeight: FontWeight.w600,
                                color: StudioTheme.accentCyan,
                              ),
                              maxLines: 1,
                              overflow: TextOverflow.ellipsis,
                            ),
                          ),
                        ],
                      ),
                    ),
                    const SizedBox(width: 8),
                    // Active selection toggle
                    Transform.scale(
                      scale: 0.8,
                      child: Switch(
                        value: isSelected,
                        activeThumbColor: Colors.white,
                        activeTrackColor: StudioTheme.primary,
                        inactiveThumbColor: StudioTheme.textMuted,
                        inactiveTrackColor: StudioTheme.surfacePane,
                        onChanged: widget.onToggleSelected,
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 8),
                // Description
                Text(
                  perspective.description,
                  style: const TextStyle(
                    fontSize: 12.0,
                    height: 1.45,
                    color: StudioTheme.textSecondary,
                  ),
                ),
                // Expandable focus questions
                if (perspective.focusQuestions.isNotEmpty) ...[
                  const SizedBox(height: 8),
                  InkWell(
                    onTap: () => setState(() => _isExpanded = !_isExpanded),
                    borderRadius: BorderRadius.circular(4),
                    child: Padding(
                      padding: const EdgeInsets.symmetric(vertical: 3),
                      child: Row(
                        children: [
                          Icon(
                            _isExpanded
                                ? Icons.keyboard_arrow_down_rounded
                                : Icons.keyboard_arrow_right_rounded,
                            size: 16,
                            color: StudioTheme.primaryLight,
                          ),
                          const SizedBox(width: 4),
                          Text(
                            'Focus Questions (${perspective.focusQuestions.length})',
                            style: const TextStyle(
                              fontSize: 11.5,
                              fontWeight: FontWeight.w600,
                              color: StudioTheme.primaryLight,
                            ),
                          ),
                        ],
                      ),
                    ),
                  ),
                  AnimatedCrossFade(
                    duration: const Duration(milliseconds: 200),
                    crossFadeState:
                        _isExpanded ? CrossFadeState.showSecond : CrossFadeState.showFirst,
                    firstChild: const SizedBox.shrink(),
                    secondChild: Padding(
                      padding: const EdgeInsets.only(top: 6, left: 6, bottom: 4),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: perspective.focusQuestions.map((question) {
                          return Padding(
                            padding: const EdgeInsets.only(bottom: 5.0),
                            child: Row(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                const Text(
                                  '• ',
                                  style: TextStyle(
                                    color: StudioTheme.primaryLight,
                                    fontSize: 12,
                                    fontWeight: FontWeight.bold,
                                  ),
                                ),
                                Expanded(
                                  child: Text(
                                    question,
                                    style: const TextStyle(
                                      fontSize: 11.5,
                                      height: 1.4,
                                      color: Color(0xFFCBD5E1),
                                      fontStyle: FontStyle.italic,
                                    ),
                                  ),
                                ),
                              ],
                            ),
                          );
                        }).toList(),
                      ),
                    ),
                  ),
                ],
              ],
            ),
          ),
        ),
      ),
    );
  }
}
