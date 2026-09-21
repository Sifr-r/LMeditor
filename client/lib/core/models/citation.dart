import 'package:flutter/material.dart';
import 'package:flutter/foundation.dart';
import '../theme.dart';

/// Semantic NLI Entailment Labels
enum NLILabel {
  entailment,
  contradiction,
  neutral,
  ungrounded;

  static NLILabel fromString(String? label) {
    if (label == null) return NLILabel.neutral;
    switch (label.toLowerCase().trim()) {
      case 'entailment':
      case 'verified':
        return NLILabel.entailment;
      case 'contradiction':
        return NLILabel.contradiction;
      case 'neutral':
        return NLILabel.neutral;
      case 'ungrounded':
        return NLILabel.ungrounded;
      default:
        throw ArgumentError.value(label, 'label', 'Invalid NLILabel value');
    }
  }

  String get displayName {
    switch (this) {
      case NLILabel.entailment:
        return 'Verified';
      case NLILabel.contradiction:
        return 'Contradiction';
      case NLILabel.neutral:
        return 'Neutral';
      case NLILabel.ungrounded:
        return 'Ungrounded';
    }
  }

  Color get color {
    switch (this) {
      case NLILabel.entailment:
        return AppColors.verifiedGreen;
      case NLILabel.contradiction:
      case NLILabel.ungrounded:
        return AppColors.contradictionRed;
      case NLILabel.neutral:
        return AppColors.neutralYellow;
    }
  }

  IconData get icon {
    switch (this) {
      case NLILabel.entailment:
        return Icons.check_circle_rounded;
      case NLILabel.contradiction:
        return Icons.cancel_rounded;
      case NLILabel.neutral:
        return Icons.help_outline_rounded;
      case NLILabel.ungrounded:
        return Icons.warning_amber_rounded;
    }
  }
}

/// Immutable model representing an inline citation and token-span lineage.
@immutable
class CitationLineage {
  final String citationId;
  final String sourceChunkId;
  final String sectionId;
  final String? sourceTitle;
  final String? prefix;
  final String exactSpan;
  final String? suffix;
  final int startOffset;
  final int endOffset;
  final double confidenceScore;
  final Map<String, dynamic> metadata;

  CitationLineage({
    required this.citationId,
    required this.sourceChunkId,
    this.sectionId = 'sec-1',
    this.sourceTitle,
    this.prefix,
    required this.exactSpan,
    this.suffix,
    required this.startOffset,
    required this.endOffset,
    required this.confidenceScore,
    this.metadata = const {},
  }) {
    if (citationId.trim().isEmpty) {
      throw ArgumentError.value(citationId, 'citationId', 'citationId cannot be empty');
    }
    if (sourceChunkId.trim().isEmpty) {
      throw ArgumentError.value(sourceChunkId, 'sourceChunkId', 'sourceChunkId cannot be empty');
    }
    if (startOffset < 0) {
      throw ArgumentError.value(startOffset, 'startOffset', 'startOffset cannot be negative');
    }
    if (endOffset < startOffset) {
      throw ArgumentError.value(endOffset, 'endOffset', 'endOffset cannot precede startOffset');
    }
    if (confidenceScore < 0.0 || confidenceScore > 1.0) {
      throw ArgumentError.value(confidenceScore, 'confidenceScore', 'confidenceScore must be between 0.0 and 1.0');
    }
  }

  String get id => citationId;
  String get chunkId => sourceChunkId;
  String get targetTokenSpan => exactSpan;
  double get confidence => confidenceScore;

  factory CitationLineage.fromJson(Map<String, dynamic> json) {
    return CitationLineage(
      citationId: (json['citation_id'] ?? json['citationId'] ?? json['id'] ?? '').toString(),
      sourceChunkId: (json['source_chunk_id'] ?? json['sourceChunkId'] ?? json['chunk_id'] ?? json['chunkId'] ?? '').toString(),
      exactSpan: (json['exact_span'] ?? json['exactSpan'] ?? json['cited_text'] ?? json['targetTokenSpan'] ?? '').toString(),
      prefix: json['prefix'] as String?,
      suffix: json['suffix'] as String?,
      startOffset: (json['start_offset'] ?? json['startOffset'] ?? json['start_char'] ?? json['startChar'] ?? 0) as int,
      endOffset: (json['end_offset'] ?? json['endOffset'] ?? json['end_char'] ?? json['endChar'] ?? 0) as int,
      confidenceScore: ((json['confidence_score'] ?? json['confidenceScore'] ?? json['confidence'] ?? 1.0) as num).toDouble(),
      metadata: json['metadata'] is Map<String, dynamic>
          ? Map<String, dynamic>.from(json['metadata'] as Map)
          : const {},
    );
  }

  Map<String, dynamic> toJson() => {
    'citation_id': citationId,
    'source_chunk_id': sourceChunkId,
    'exact_span': exactSpan,
    if (prefix != null) 'prefix': prefix,
    if (suffix != null) 'suffix': suffix,
    'start_offset': startOffset,
    'end_offset': endOffset,
    'confidence_score': confidenceScore,
    'metadata': metadata,
  };

  CitationLineage copyWith({
    String? citationId,
    String? sourceChunkId,
    String? exactSpan,
    String? prefix,
    String? suffix,
    int? startOffset,
    int? endOffset,
    double? confidenceScore,
    Map<String, dynamic>? metadata,
  }) {
    return CitationLineage(
      citationId: citationId ?? this.citationId,
      sourceChunkId: sourceChunkId ?? this.sourceChunkId,
      exactSpan: exactSpan ?? this.exactSpan,
      prefix: prefix ?? this.prefix,
      suffix: suffix ?? this.suffix,
      startOffset: startOffset ?? this.startOffset,
      endOffset: endOffset ?? this.endOffset,
      confidenceScore: confidenceScore ?? this.confidenceScore,
      metadata: metadata ?? this.metadata,
    );
  }

  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      other is CitationLineage &&
          runtimeType == other.runtimeType &&
          citationId == other.citationId &&
          sourceChunkId == other.sourceChunkId &&
          exactSpan == other.exactSpan &&
          prefix == other.prefix &&
          suffix == other.suffix &&
          startOffset == other.startOffset &&
          endOffset == other.endOffset &&
          confidenceScore == other.confidenceScore &&
          mapEquals(metadata, other.metadata);

  @override
  int get hashCode => Object.hash(
        citationId,
        sourceChunkId,
        exactSpan,
        prefix,
        suffix,
        startOffset,
        endOffset,
        confidenceScore,
      );

  @override
  String toString() =>
      'CitationLineage(id: $citationId, chunkId: $sourceChunkId, span: $startOffset..$endOffset, score: $confidenceScore)';
}
