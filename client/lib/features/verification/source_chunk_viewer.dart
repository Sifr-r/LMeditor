import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import '../../core/models/chunk.dart';
import '../../core/models/citation.dart';
import '../../core/theme.dart';

/// Ground Truth Passage Inspector.
///
/// Displays:
/// - Premise [SourceChunk] content with highlighted character offsets from [CitationLineage].
/// - Source document metadata, author, and SHA-256 cryptographic hash.
class SourceChunkViewer extends StatelessWidget {
  final SourceChunk? chunk;
  final CitationLineage? lineage;
  final VoidCallback? onClose;

  const SourceChunkViewer({
    super.key,
    required this.chunk,
    this.lineage,
    this.onClose,
  });

  void _copyHash(BuildContext context, String hash) {
    Clipboard.setData(ClipboardData(text: hash));
    ScaffoldMessenger.of(context).showSnackBar(
      const SnackBar(
        content: Text('SHA-256 Hash copied to clipboard'),
        duration: Duration(seconds: 1),
        behavior: SnackBarBehavior.floating,
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final activeChunk = chunk;
    if (activeChunk == null) {
      return Container(
        color: StudioTheme.surfacePane,
        padding: const EdgeInsets.all(20),
        child: const Center(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(Icons.find_in_page_outlined, size: 36, color: StudioTheme.textMuted),
              SizedBox(height: 8),
              Text(
                'No Source Chunk Selected',
                style: TextStyle(fontSize: 13, color: StudioTheme.textSecondary),
              ),
              SizedBox(height: 4),
              Text(
                'Click a citation chip or claim to inspect its premise passage.',
                textAlign: TextAlign.center,
                style: TextStyle(fontSize: 11.5, color: StudioTheme.textMuted),
              ),
            ],
          ),
        ),
      );
    }

    return Container(
      decoration: const BoxDecoration(
        color: StudioTheme.surfacePane,
        border: Border(top: BorderSide(color: StudioTheme.border, width: 1)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          _buildHeader(context, activeChunk),
          Expanded(
            child: SingleChildScrollView(
              padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  _buildMetadataBar(context, activeChunk),
                  const SizedBox(height: 10),
                  const Text(
                    'GROUND TRUTH PASSAGE (PREMISE):',
                    style: TextStyle(
                      fontFamily: 'monospace',
                      fontSize: 10.5,
                      fontWeight: FontWeight.w700,
                      color: StudioTheme.textSecondary,
                      letterSpacing: 0.5,
                    ),
                  ),
                  const SizedBox(height: 6),
                  _buildPassageText(activeChunk),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildHeader(BuildContext context, SourceChunk activeChunk) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
      decoration: const BoxDecoration(
        color: Color(0xFF131C2E),
        border: Border(bottom: BorderSide(color: StudioTheme.borderSubtle)),
      ),
      child: Row(
        children: [
          const Icon(Icons.verified_outlined, size: 15, color: StudioTheme.verifiedEmerald),
          const SizedBox(width: 6),
          Expanded(
            child: Text(
              activeChunk.documentTitle,
              style: const TextStyle(
                fontSize: 12.5,
                fontWeight: FontWeight.w700,
                color: StudioTheme.textPrimary,
              ),
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
            ),
          ),
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
            decoration: BoxDecoration(
              color: StudioTheme.surfaceCard,
              borderRadius: BorderRadius.circular(4),
              border: Border.all(color: StudioTheme.borderSubtle),
            ),
            child: Text(
              '[#${activeChunk.chunkId}]',
              style: const TextStyle(
                fontFamily: 'monospace',
                fontSize: 10.5,
                fontWeight: FontWeight.w700,
                color: StudioTheme.accentCyan,
              ),
            ),
          ),
          if (onClose != null) ...[
            const SizedBox(width: 6),
            IconButton(
              icon: const Icon(Icons.close_rounded, size: 16, color: StudioTheme.textMuted),
              visualDensity: VisualDensity.compact,
              padding: EdgeInsets.zero,
              constraints: const BoxConstraints(),
              onPressed: onClose,
            ),
          ],
        ],
      ),
    );
  }

  Widget _buildMetadataBar(BuildContext context, SourceChunk activeChunk) {
    return Container(
      padding: const EdgeInsets.all(8),
      decoration: BoxDecoration(
        color: StudioTheme.surfaceCard,
        borderRadius: BorderRadius.circular(6),
        border: Border.all(color: StudioTheme.borderSubtle),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              const Icon(Icons.fingerprint_rounded, size: 13, color: StudioTheme.textMuted),
              const SizedBox(width: 4),
              const Text(
                'SHA-256:',
                style: TextStyle(fontFamily: 'monospace', fontSize: 10.5, color: StudioTheme.textMuted),
              ),
              const SizedBox(width: 6),
              Expanded(
                child: Text(
                  activeChunk.sha256Hash.isEmpty ? 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855' : activeChunk.sha256Hash,
                  style: const TextStyle(
                    fontFamily: 'monospace',
                    fontSize: 10.0,
                    color: StudioTheme.accentCyan,
                  ),
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                ),
              ),
              InkWell(
                onTap: () => _copyHash(context, activeChunk.sha256Hash),
                borderRadius: BorderRadius.circular(4),
                child: const Padding(
                  padding: EdgeInsets.all(2),
                  child: Icon(Icons.copy_rounded, size: 12, color: StudioTheme.textSecondary),
                ),
              ),
            ],
          ),
          const SizedBox(height: 4),
          Row(
            children: [
              const Icon(Icons.pin_drop_outlined, size: 13, color: StudioTheme.textMuted),
              const SizedBox(width: 4),
              Text(
                'Offsets: chars ${activeChunk.startChar}–${activeChunk.endChar}',
                style: const TextStyle(
                  fontFamily: 'monospace',
                  fontSize: 10.5,
                  color: StudioTheme.textSecondary,
                ),
              ),
              if (lineage != null) ...[
                const SizedBox(width: 10),
                const Icon(Icons.timeline_rounded, size: 13, color: StudioTheme.verifiedEmerald),
                const SizedBox(width: 4),
                Text(
                  'Fidelity: ${(lineage!.confidence * 100).toStringAsFixed(1)}%',
                  style: const TextStyle(
                    fontFamily: 'monospace',
                    fontSize: 10.5,
                    fontWeight: FontWeight.w600,
                    color: StudioTheme.verifiedEmerald,
                  ),
                ),
              ],
            ],
          ),
        ],
      ),
    );
  }

  Widget _buildPassageText(SourceChunk activeChunk) {
    final text = activeChunk.content;
    final lin = lineage;

    // Check if we have exact highlight boundaries
    int start = -1;
    int end = -1;

    if (lin != null) {
      if (lin.startOffset >= 0 && lin.endOffset > lin.startOffset && lin.endOffset <= text.length) {
        start = lin.startOffset;
        end = lin.endOffset;
      } else if (lin.targetTokenSpan.isNotEmpty && text.contains(lin.targetTokenSpan)) {
        start = text.indexOf(lin.targetTokenSpan);
        end = start + lin.targetTokenSpan.length;
      }
    }

    if (start != -1 && end != -1) {
      final pre = text.substring(0, start);
      final highlighted = text.substring(start, end);
      final post = text.substring(end);

      return Container(
        padding: const EdgeInsets.all(10),
        decoration: BoxDecoration(
          color: const Color(0xFF0D1424),
          borderRadius: BorderRadius.circular(6),
          border: Border.all(color: StudioTheme.borderSubtle),
        ),
        child: SelectableText.rich(
          TextSpan(
            style: const TextStyle(
              fontSize: 12.5,
              height: 1.6,
              color: Color(0xFFCBD5E1),
              letterSpacing: 0.1,
            ),
            children: [
              TextSpan(text: pre),
              WidgetSpan(
                alignment: PlaceholderAlignment.middle,
                child: Container(
                  padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 1),
                  decoration: BoxDecoration(
                    color: StudioTheme.neutralAmber.withValues(alpha: 0.28),
                    borderRadius: BorderRadius.circular(3),
                    border: Border.all(
                      color: StudioTheme.neutralAmber.withValues(alpha: 0.8),
                      width: 1,
                    ),
                  ),
                  child: Text(
                    highlighted,
                    style: const TextStyle(
                      fontSize: 12.5,
                      fontWeight: FontWeight.w600,
                      color: Colors.white,
                    ),
                  ),
                ),
              ),
              TextSpan(text: post),
            ],
          ),
        ),
      );
    }

    return Container(
      padding: const EdgeInsets.all(10),
      decoration: BoxDecoration(
        color: const Color(0xFF0D1424),
        borderRadius: BorderRadius.circular(6),
        border: Border.all(color: StudioTheme.borderSubtle),
      ),
      child: SelectableText(
        text,
        style: const TextStyle(
          fontSize: 12.5,
          height: 1.6,
          color: Color(0xFFCBD5E1),
        ),
      ),
    );
  }
}
