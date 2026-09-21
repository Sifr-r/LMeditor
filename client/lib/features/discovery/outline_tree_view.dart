import 'package:flutter/material.dart';
import '../../core/models/outline.dart';
import '../../core/theme.dart';

/// Interactive hierarchical tree view for document outlines ([List<OutlineNode>]).
///
/// Features:
/// - Indentation based on node level.
/// - Displays section title, section goal, assigned chunk count chip, and completion status checkmark.
/// - Selection highlight with [onNodeSelected] callback.
/// - "Generate Outline" trigger button and progress indicator.
class OutlineTreeView extends StatelessWidget {
  final List<OutlineNode> nodes;
  final String? selectedNodeId;
  final ValueChanged<OutlineNode>? onNodeSelected;
  final VoidCallback? onGenerateOutline;
  final bool isGeneratingOutline;

  const OutlineTreeView({
    super.key,
    required this.nodes,
    this.selectedNodeId,
    this.onNodeSelected,
    this.onGenerateOutline,
    this.isGeneratingOutline = false,
  });

  @override
  Widget build(BuildContext context) {
    // Flatten hierarchy with level-based indentation
    final flatList = <OutlineNode>[];
    void collect(List<OutlineNode> items) {
      for (final item in items) {
        flatList.add(item);
        if (item.children.isNotEmpty) {
          collect(item.children);
        }
      }
    }
    collect(nodes);

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        _buildHeader(),
        if (isGeneratingOutline)
          const LinearProgressIndicator(
            minHeight: 2.5,
            backgroundColor: StudioTheme.surfacePane,
            valueColor: AlwaysStoppedAnimation<Color>(StudioTheme.primaryLight),
          ),
        Expanded(
          child: flatList.isEmpty
              ? _buildEmptyState()
              : ListView.separated(
                  padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
                  itemCount: flatList.length,
                  separatorBuilder: (context, index) => const SizedBox(height: 4),
                  itemBuilder: (context, index) {
                    final node = flatList[index];
                    final isSelected = node.id == selectedNodeId;
                    return _buildNodeTile(node, isSelected);
                  },
                ),
        ),
      ],
    );
  }

  Widget _buildHeader() {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
      decoration: const BoxDecoration(
        color: StudioTheme.surfacePane,
        border: Border(bottom: BorderSide(color: StudioTheme.borderSubtle)),
      ),
      child: Row(
        children: [
          const Icon(Icons.account_tree_outlined, size: 16, color: StudioTheme.textSecondary),
          const SizedBox(width: 8),
          const Expanded(
            child: Text(
              'Document Outline',
              style: TextStyle(
                fontSize: 13,
                fontWeight: FontWeight.w700,
                color: StudioTheme.textPrimary,
                letterSpacing: 0.2,
              ),
            ),
          ),
          if (onGenerateOutline != null)
            TextButton.icon(
              onPressed: isGeneratingOutline ? null : onGenerateOutline,
              icon: isGeneratingOutline
                  ? const SizedBox(
                      width: 12,
                      height: 12,
                      child: CircularProgressIndicator(strokeWidth: 1.5),
                    )
                  : const Icon(Icons.auto_awesome_rounded, size: 14),
              label: Text(
                isGeneratingOutline ? 'Generating...' : 'Generate Outline',
                style: const TextStyle(fontSize: 11.5, fontWeight: FontWeight.w600),
              ),
              style: TextButton.styleFrom(
                padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                visualDensity: VisualDensity.compact,
                foregroundColor: StudioTheme.primaryLight,
              ),
            ),
        ],
      ),
    );
  }

  Widget _buildEmptyState() {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(20),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Icon(Icons.alt_route_rounded, size: 36, color: StudioTheme.textMuted),
            const SizedBox(height: 10),
            const Text(
              'No Outline Generated',
              style: TextStyle(fontSize: 13.5, fontWeight: FontWeight.w600, color: StudioTheme.textSecondary),
            ),
            const SizedBox(height: 4),
            const Text(
              'Synthesize STORM personas to build section blueprints and candidate chunk allocations.',
              textAlign: TextAlign.center,
              style: TextStyle(fontSize: 11.5, color: StudioTheme.textMuted, height: 1.4),
            ),
            const SizedBox(height: 12),
            if (onGenerateOutline != null)
              ElevatedButton.icon(
                onPressed: onGenerateOutline,
                icon: const Icon(Icons.schema_rounded, size: 14),
                label: const Text('Generate Outline Tree', style: TextStyle(fontSize: 12)),
                style: ElevatedButton.styleFrom(
                  backgroundColor: StudioTheme.primary,
                  foregroundColor: Colors.white,
                  padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                ),
              ),
          ],
        ),
      ),
    );
  }

  Widget _buildNodeTile(OutlineNode node, bool isSelected) {
    final indentPadding = (node.level - 1) * 16.0;

    final bgColor = isSelected
        ? StudioTheme.primary.withValues(alpha: 0.18)
        : Colors.transparent;

    final borderColor = isSelected
        ? StudioTheme.primaryLight.withValues(alpha: 0.6)
        : Colors.transparent;

    return Container(
      margin: EdgeInsets.only(left: indentPadding),
      decoration: BoxDecoration(
        color: bgColor,
        borderRadius: BorderRadius.circular(6),
        border: Border.all(color: borderColor, width: 1),
      ),
      child: Material(
        color: Colors.transparent,
        child: InkWell(
          onTap: () => onNodeSelected?.call(node),
          borderRadius: BorderRadius.circular(6),
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 7),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.center,
              children: [
                // Completion checkmark or status icon
                _buildStatusIcon(node),
                const SizedBox(width: 8),
                // Section Title & Goal
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Text(
                        node.title,
                        style: TextStyle(
                          fontSize: node.level == 1 ? 12.5 : 12.0,
                          fontWeight: node.level == 1 ? FontWeight.w700 : FontWeight.w500,
                          color: isSelected ? Colors.white : StudioTheme.textPrimary,
                        ),
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                      ),
                      if (node.goal.isNotEmpty) ...[
                        const SizedBox(height: 1.5),
                        Text(
                          node.goal,
                          style: const TextStyle(
                            fontSize: 10.5,
                            color: StudioTheme.textMuted,
                            height: 1.2,
                          ),
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                        ),
                      ],
                    ],
                  ),
                ),
                const SizedBox(width: 6),
                // Assigned chunks chip
                if (node.assignedChunkIds.isNotEmpty)
                  Container(
                    padding: const EdgeInsets.symmetric(horizontal: 5, vertical: 1.5),
                    decoration: BoxDecoration(
                      color: StudioTheme.surfaceCard,
                      borderRadius: BorderRadius.circular(4),
                      border: Border.all(color: StudioTheme.borderSubtle),
                    ),
                    child: Text(
                      '${node.assignedChunkIds.length} chunks',
                      style: const TextStyle(
                        fontFamily: 'monospace',
                        fontSize: 9.5,
                        fontWeight: FontWeight.w600,
                        color: StudioTheme.accentCyan,
                      ),
                    ),
                  ),
              ],
            ),
          ),
        ),
      ),
    );
  }

  Widget _buildStatusIcon(OutlineNode node) {
    if (node.isCompleted) {
      return const Icon(
        Icons.check_circle_rounded,
        size: 15,
        color: StudioTheme.verifiedEmerald,
      );
    }
    return const Icon(
      Icons.radio_button_unchecked_rounded,
      size: 14,
      color: StudioTheme.textMuted,
    );
  }
}
