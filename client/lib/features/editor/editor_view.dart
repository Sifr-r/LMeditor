import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import '../../core/models/claim.dart';
import '../../core/models/citation.dart';
import '../../core/models/manuscript.dart';
import '../../core/theme.dart';
import 'citation_badge.dart';

/// Rich text expository manuscript viewer with real-time streaming text support.
///
/// Converts formatted text containing citation tags:
/// - `{{cite:citationId:chunkId}}`
/// - `[#chunkId]`
/// into inline [WidgetSpan] [CitationBadge] chips.
class EditorView extends StatefulWidget {
  final Manuscript manuscript;
  final List<Claim> claims;
  final String? selectedChunkId;
  final String? selectedClaimId;
  final void Function(String chunkId, String? citationId)? onCitationTap;
  final bool isStreaming;
  final VoidCallback? onToggleStream;
  final VoidCallback? onClear;

  const EditorView({
    super.key,
    required this.manuscript,
    required this.claims,
    this.selectedChunkId,
    this.selectedClaimId,
    this.onCitationTap,
    this.isStreaming = false,
    this.onToggleStream,
    this.onClear,
  });

  @override
  State<EditorView> createState() => _EditorViewState();
}

class _EditorViewState extends State<EditorView> {
  final ScrollController _scrollController = ScrollController();
  bool _autoScrollEnabled = true;
  bool _showRawMarkdown = false;
  double _fontSizeMultiplier = 1.0;

  @override
  void initState() {
    super.initState();
    _scrollController.addListener(_onScroll);
  }

  @override
  void dispose() {
    _scrollController.removeListener(_onScroll);
    _scrollController.dispose();
    super.dispose();
  }

  void _onScroll() {
    if (!_scrollController.hasClients) return;
    final maxScroll = _scrollController.position.maxScrollExtent;
    final currentScroll = _scrollController.position.pixels;
    // If user scrolls up by more than 80px, pause auto-scrolling
    if (maxScroll - currentScroll > 80.0) {
      if (_autoScrollEnabled) {
        setState(() => _autoScrollEnabled = false);
      }
    } else {
      if (!_autoScrollEnabled) {
        setState(() => _autoScrollEnabled = true);
      }
    }
  }

  @override
  void didUpdateWidget(covariant EditorView oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (widget.isStreaming &&
        _autoScrollEnabled &&
        widget.manuscript.content.length != oldWidget.manuscript.content.length) {
      _scrollToBottom();
    }
  }

  void _scrollToBottom() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (_scrollController.hasClients && _autoScrollEnabled) {
        _scrollController.animateTo(
          _scrollController.position.maxScrollExtent,
          duration: const Duration(milliseconds: 140),
          curve: Curves.easeOut,
        );
      }
    });
  }

  void _resumeAutoScroll() {
    setState(() => _autoScrollEnabled = true);
    _scrollToBottom();
  }

  void _copyToClipboard() {
    Clipboard.setData(ClipboardData(text: widget.manuscript.content));
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: const Row(
          children: [
            Icon(Icons.check_circle_outline, color: StudioTheme.verifiedEmerald, size: 18),
            SizedBox(width: 8),
            Text('Manuscript copied to clipboard as Markdown'),
          ],
        ),
        backgroundColor: StudioTheme.surfacePane,
        behavior: SnackBarBehavior.floating,
        duration: const Duration(seconds: 2),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final groundingPercent = (widget.manuscript.groundingScore * 100).toStringAsFixed(1);
    final groundingColor = widget.manuscript.groundingScore >= 0.85
        ? StudioTheme.verifiedEmerald
        : (widget.manuscript.groundingScore >= 0.70
            ? StudioTheme.neutralAmber
            : StudioTheme.contradictionCrimson);

    return Container(
      color: StudioTheme.background,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          _buildHeader(groundingPercent, groundingColor),
          _buildToolbar(),
          Expanded(
            child: Stack(
              children: [
                _showRawMarkdown ? _buildRawMarkdownView() : _buildRenderedManuscriptView(),
                if (widget.isStreaming && !_autoScrollEnabled)
                  Positioned(
                    bottom: 18,
                    right: 24,
                    child: _buildResumeScrollPill(),
                  ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildHeader(String groundingPercent, Color groundingColor) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 16),
      decoration: const BoxDecoration(
        color: StudioTheme.surfacePane,
        border: Border(bottom: BorderSide(color: StudioTheme.border, width: 1)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.center,
            children: [
              Expanded(
                child: Text(
                  widget.manuscript.title,
                  style: const TextStyle(
                    fontSize: 20,
                    fontWeight: FontWeight.w700,
                    letterSpacing: -0.3,
                    color: StudioTheme.textPrimary,
                  ),
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                ),
              ),
              const SizedBox(width: 12),
              // Grounding Badge
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
                decoration: BoxDecoration(
                  color: groundingColor.withValues(alpha: 0.14),
                  borderRadius: BorderRadius.circular(6),
                  border: Border.all(color: groundingColor.withValues(alpha: 0.5), width: 1),
                ),
                child: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Icon(Icons.shield_outlined, size: 14, color: groundingColor),
                    const SizedBox(width: 5),
                    Text(
                      '$groundingPercent% Grounded',
                      style: TextStyle(
                        fontFamily: 'monospace',
                        fontSize: 11.5,
                        fontWeight: FontWeight.w700,
                        color: groundingColor,
                      ),
                    ),
                  ],
                ),
              ),
            ],
          ),
          const SizedBox(height: 8),
          Row(
            children: [
              const Icon(Icons.menu_book_rounded, size: 14, color: StudioTheme.textMuted),
              const SizedBox(width: 6),
              Flexible(
                child: Text(
                  widget.manuscript.currentSection,
                  style: const TextStyle(
                    fontSize: 13,
                    fontWeight: FontWeight.w500,
                    color: StudioTheme.textSecondary,
                  ),
                  overflow: TextOverflow.ellipsis,
                ),
              ),
              const SizedBox(width: 16),
              _buildMetaChip(Icons.numbers_rounded, '${widget.manuscript.wordCount} words'),
              const SizedBox(width: 8),
              _buildMetaChip(Icons.schedule_rounded, '${widget.manuscript.readingTimeMinutes} min read'),
              if (widget.isStreaming) ...[
                const SizedBox(width: 12),
                _buildLiveStreamingBadge(),
              ],
            ],
          ),
        ],
      ),
    );
  }

  Widget _buildMetaChip(IconData icon, String text) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
      decoration: BoxDecoration(
        color: StudioTheme.surfaceCard,
        borderRadius: BorderRadius.circular(4),
        border: Border.all(color: StudioTheme.borderSubtle),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(icon, size: 11.5, color: StudioTheme.textMuted),
          const SizedBox(width: 4),
          Text(
            text,
            style: const TextStyle(
              fontSize: 11.5,
              color: StudioTheme.textSecondary,
              fontFamily: 'monospace',
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildLiveStreamingBadge() {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2.5),
      decoration: BoxDecoration(
        color: StudioTheme.primary.withValues(alpha: 0.15),
        borderRadius: BorderRadius.circular(4),
        border: Border.all(color: StudioTheme.primary.withValues(alpha: 0.5)),
      ),
      child: const Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          SizedBox(
            width: 8,
            height: 8,
            child: CircularProgressIndicator(
              strokeWidth: 1.8,
              valueColor: AlwaysStoppedAnimation<Color>(StudioTheme.primaryLight),
            ),
          ),
          SizedBox(width: 6),
          Text(
            'STREAMING DRAFT...',
            style: TextStyle(
              fontFamily: 'monospace',
              fontSize: 10.5,
              fontWeight: FontWeight.w700,
              color: StudioTheme.primaryLight,
              letterSpacing: 0.4,
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildToolbar() {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 6),
      decoration: const BoxDecoration(
        color: Color(0xFF0F1728),
        border: Border(bottom: BorderSide(color: StudioTheme.borderSubtle, width: 1)),
      ),
      child: SingleChildScrollView(
        scrollDirection: Axis.horizontal,
        child: Row(
          children: [
            // Stream Toggle Button
          if (widget.onToggleStream != null) ...[
            FilledButton.tonalIcon(
              onPressed: widget.onToggleStream,
              icon: Icon(
                widget.isStreaming ? Icons.pause_rounded : Icons.play_arrow_rounded,
                size: 16,
              ),
              label: Text(widget.isStreaming ? 'Pause Generation' : 'Stream Next Section'),
              style: FilledButton.styleFrom(
                padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
                textStyle: const TextStyle(fontSize: 12.5, fontWeight: FontWeight.w600),
                backgroundColor: widget.isStreaming
                    ? StudioTheme.neutralAmber.withValues(alpha: 0.2)
                    : StudioTheme.primary.withValues(alpha: 0.25),
                foregroundColor: widget.isStreaming ? StudioTheme.neutralAmber : StudioTheme.textPrimary,
              ),
            ),
            const SizedBox(width: 8),
          ],
          // View Toggle (Rich vs Raw Markdown)
          SegmentedButton<bool>(
            segments: const [
              ButtonSegment<bool>(
                value: false,
                label: Text('Expository Rich', style: TextStyle(fontSize: 11.5)),
                icon: Icon(Icons.auto_stories_rounded, size: 14),
              ),
              ButtonSegment<bool>(
                value: true,
                label: Text('Raw Markdown', style: TextStyle(fontSize: 11.5)),
                icon: Icon(Icons.code_rounded, size: 14),
              ),
            ],
            selected: {_showRawMarkdown},
            onSelectionChanged: (set) => setState(() => _showRawMarkdown = set.first),
            style: SegmentedButton.styleFrom(
              padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 2),
              visualDensity: VisualDensity.compact,
              backgroundColor: StudioTheme.surfaceCard,
              selectedBackgroundColor: StudioTheme.surfaceHover,
              side: const BorderSide(color: StudioTheme.borderSubtle),
            ),
          ),
          const SizedBox(width: 16),
          // Font zoom controls
          IconButton(
            tooltip: 'Decrease font size',
            icon: const Icon(Icons.remove_rounded, size: 16, color: StudioTheme.textSecondary),
            visualDensity: VisualDensity.compact,
            onPressed: () {
              if (_fontSizeMultiplier > 0.85) {
                setState(() => _fontSizeMultiplier -= 0.1);
              }
            },
          ),
          Text(
            '${(_fontSizeMultiplier * 100).toInt()}%',
            style: const TextStyle(
              fontSize: 11,
              fontFamily: 'monospace',
              color: StudioTheme.textMuted,
            ),
          ),
          IconButton(
            tooltip: 'Increase font size',
            icon: const Icon(Icons.add_rounded, size: 16, color: StudioTheme.textSecondary),
            visualDensity: VisualDensity.compact,
            onPressed: () {
              if (_fontSizeMultiplier < 1.35) {
                setState(() => _fontSizeMultiplier += 0.1);
              }
            },
          ),
          const SizedBox(width: 8),
          // Copy to Markdown button
          OutlinedButton.icon(
            onPressed: widget.manuscript.content.isEmpty ? null : _copyToClipboard,
            icon: const Icon(Icons.copy_rounded, size: 14),
            label: const Text('Copy Markdown', style: TextStyle(fontSize: 12)),
            style: OutlinedButton.styleFrom(
              padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
              visualDensity: VisualDensity.compact,
              side: const BorderSide(color: StudioTheme.border),
              foregroundColor: StudioTheme.textPrimary,
            ),
          ),
            if (widget.onClear != null) ...[
              const SizedBox(width: 8),
              IconButton(
                tooltip: 'Clear manuscript',
                icon: const Icon(Icons.delete_sweep_outlined, size: 18, color: StudioTheme.textMuted),
                onPressed: widget.onClear,
              ),
            ],
          ],
        ),
      ),
    );
  }

  Widget _buildResumeScrollPill() {
    return Material(
      color: Colors.transparent,
      child: InkWell(
        onTap: _resumeAutoScroll,
        borderRadius: BorderRadius.circular(20),
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 8),
          decoration: BoxDecoration(
            color: StudioTheme.primary,
            borderRadius: BorderRadius.circular(20),
            boxShadow: [
              BoxShadow(
                color: Colors.black.withValues(alpha: 0.4),
                blurRadius: 10,
                offset: const Offset(0, 4),
              ),
            ],
          ),
          child: const Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(Icons.arrow_downward_rounded, size: 15, color: Colors.white),
              SizedBox(width: 6),
              Text(
                'Resume auto-scroll',
                style: TextStyle(
                  fontSize: 12,
                  fontWeight: FontWeight.w600,
                  color: Colors.white,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _buildRawMarkdownView() {
    return Container(
      color: StudioTheme.background,
      padding: const EdgeInsets.all(24),
      child: SelectableText(
        widget.manuscript.content.isEmpty
            ? 'No manuscript drafted yet. Click "Stream Next Section" or run the discovery pipeline to generate.'
            : widget.manuscript.content,
        style: TextStyle(
          fontFamily: 'monospace',
          fontSize: 13.5 * _fontSizeMultiplier,
          height: 1.6,
          color: StudioTheme.textSecondary,
        ),
      ),
    );
  }

  Widget _buildRenderedManuscriptView() {
    if (widget.manuscript.content.isEmpty) {
      return Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Icon(Icons.article_outlined, size: 48, color: StudioTheme.textMuted),
            const SizedBox(height: 12),
            const Text(
              'Manuscript Draft Ready',
              style: TextStyle(fontSize: 16, fontWeight: FontWeight.w600, color: StudioTheme.textSecondary),
            ),
            const SizedBox(height: 6),
            const Text(
              'Start real-time generation or select a section from the outline tree to draft.',
              style: TextStyle(fontSize: 13, color: StudioTheme.textMuted),
            ),
            const SizedBox(height: 16),
            if (widget.onToggleStream != null)
              ElevatedButton.icon(
                onPressed: widget.onToggleStream,
                icon: const Icon(Icons.auto_awesome_rounded, size: 16),
                label: const Text('Generate Section with Grounded Citations'),
                style: ElevatedButton.styleFrom(
                  backgroundColor: StudioTheme.primary,
                  foregroundColor: Colors.white,
                ),
              ),
          ],
        ),
      );
    }

    final paragraphs = widget.manuscript.content.split('\n\n');

    return SelectionArea(
      child: ListView.builder(
        controller: _scrollController,
        padding: const EdgeInsets.symmetric(horizontal: 40, vertical: 28),
        itemCount: paragraphs.length,
        itemBuilder: (context, index) {
          final paragraph = paragraphs[index].trim();
          if (paragraph.isEmpty) return const SizedBox.shrink();
          return Padding(
            padding: const EdgeInsets.only(bottom: 20.0),
            child: _buildParagraphWidget(paragraph),
          );
        },
      ),
    );
  }

  Widget _buildParagraphWidget(String text) {
    // Header check
    if (text.startsWith('# ')) {
      return Text(
        text.substring(2).trim(),
        style: TextStyle(
          fontSize: 24 * _fontSizeMultiplier,
          fontWeight: FontWeight.w800,
          color: StudioTheme.textPrimary,
          letterSpacing: -0.5,
          height: 1.3,
        ),
      );
    } else if (text.startsWith('## ')) {
      return Text(
        text.substring(3).trim(),
        style: TextStyle(
          fontSize: 19 * _fontSizeMultiplier,
          fontWeight: FontWeight.w700,
          color: StudioTheme.textPrimary,
          letterSpacing: -0.3,
          height: 1.4,
        ),
      );
    } else if (text.startsWith('### ')) {
      return Text(
        text.substring(4).trim(),
        style: TextStyle(
          fontSize: 16 * _fontSizeMultiplier,
          fontWeight: FontWeight.w600,
          color: StudioTheme.primaryLight,
          height: 1.4,
        ),
      );
    }

    // Body paragraph with inline citations
    final inlineSpans = _parseInlineSpans(text);
    return Text.rich(
      TextSpan(children: inlineSpans),
      style: TextStyle(
        fontSize: 15.0 * _fontSizeMultiplier,
        height: 1.75,
        color: const Color(0xFFE2E8F0),
        letterSpacing: 0.15,
      ),
    );
  }

  List<InlineSpan> _parseInlineSpans(String text) {
    final spans = <InlineSpan>[];
    // Regex matching both {{cite:citId:chunkId}} and [#chunkId]
    final citationPattern = RegExp(r'\{\{cite:([^:]+):([^}]+)\}\}|\[#([a-zA-Z0-9_\-\.]+)\]');

    int lastIndex = 0;
    for (final match in citationPattern.allMatches(text)) {
      if (match.start > lastIndex) {
        final plainSegment = text.substring(lastIndex, match.start);
        spans.addAll(_parseMarkdownFormatting(plainSegment));
      }

      String? citationId;
      String chunkId;

      if (match.group(1) != null) {
        // Form: {{cite:citId:chunkId}}
        citationId = match.group(1);
        chunkId = match.group(2) ?? '';
      } else {
        // Form: [#chunkId]
        chunkId = match.group(3) ?? '';
      }

      // Look up corresponding claim if present
      Claim? matchedClaim;
      for (final claim in widget.claims) {
        if (claim.chunkId == chunkId || (citationId != null && claim.citationId == citationId)) {
          matchedClaim = claim;
          break;
        }
      }

      final nliLabel = matchedClaim?.nliLabel ?? NLILabel.entailment;
      final confidence = matchedClaim?.confidence ?? 0.96;
      final isSelected = (widget.selectedChunkId == chunkId) ||
          (matchedClaim != null && widget.selectedClaimId == matchedClaim.claimId);

      spans.add(
        WidgetSpan(
          alignment: PlaceholderAlignment.middle,
          baseline: TextBaseline.alphabetic,
          child: CitationBadge(
            chunkId: chunkId,
            citationId: citationId,
            nliLabel: nliLabel,
            confidence: confidence,
            isSelected: isSelected,
            onTap: () {
              widget.onCitationTap?.call(chunkId, citationId);
            },
          ),
        ),
      );

      lastIndex = match.end;
    }

    if (lastIndex < text.length) {
      final remaining = text.substring(lastIndex);
      spans.addAll(_parseMarkdownFormatting(remaining));
    }

    return spans;
  }

  List<InlineSpan> _parseMarkdownFormatting(String text) {
    final spans = <InlineSpan>[];
    final boldRegex = RegExp(r'\*\*([^*]+)\*\*');
    int last = 0;

    for (final m in boldRegex.allMatches(text)) {
      if (m.start > last) {
        spans.add(TextSpan(text: text.substring(last, m.start)));
      }
      spans.add(
        TextSpan(
          text: m.group(1),
          style: const TextStyle(fontWeight: FontWeight.bold, color: Colors.white),
        ),
      );
      last = m.end;
    }

    if (last < text.length) {
      spans.add(TextSpan(text: text.substring(last)));
    }

    return spans;
  }
}
