import 'package:flutter/foundation.dart';
import 'citation.dart';

/// Immutable model representing an extracted factual claim with its NLI entailment score.
@immutable
class Claim {
  final String claimId;
  final String sentence;
  final String proposition;
  final List<String> citationIds;
  final double entailmentScore;
  final NLILabel label;
  final String? premiseChunkId;
  final String? explanation;
  final String sectionId;
  final bool? approved;
  final int? spanStart;
  final int? spanEnd;

  Claim({
    required this.claimId,
    required this.sentence,
    required this.proposition,
    this.citationIds = const [],
    required this.entailmentScore,
    required this.label,
    this.premiseChunkId,
    this.explanation,
    this.sectionId = '',
    this.approved,
    this.spanStart,
    this.spanEnd,
  }) {
    if (claimId.trim().isEmpty) {
      throw ArgumentError.value(claimId, 'claimId', 'claimId cannot be empty');
    }
    if (sentence.trim().isEmpty) {
      throw ArgumentError.value(sentence, 'sentence', 'sentence cannot be empty');
    }
    if (proposition.trim().isEmpty) {
      throw ArgumentError.value(proposition, 'proposition', 'proposition cannot be empty');
    }
    if (entailmentScore < 0.0 || entailmentScore > 1.0) {
      throw ArgumentError.value(entailmentScore, 'entailmentScore', 'entailmentScore must be between 0.0 and 1.0');
    }
  }

  bool get isVerified => label == NLILabel.entailment && entailmentScore >= 0.70;
  bool get isContradiction => label == NLILabel.contradiction;
  bool get isUngrounded => label == NLILabel.contradiction || label == NLILabel.ungrounded;

  String get id => claimId;
  String get fullSentence => sentence;
  String get claimText => proposition;
  double get confidence => entailmentScore;
  String get chunkId => premiseChunkId ?? (citationIds.isNotEmpty ? citationIds.first : '');
  String get citationId => citationIds.isNotEmpty ? citationIds.first : '';
  NLILabel get nliLabel => label;

  factory Claim.fromJson(Map<String, dynamic> json) {
    final rawCitations = json['citation_ids'] ?? json['citationIds'];
    final List<String> parsedCitationIds;
    if (rawCitations is List) {
      parsedCitationIds = rawCitations.map((e) => e.toString()).toList();
    } else if (json['citation_id'] != null || json['citationId'] != null) {
      parsedCitationIds = [(json['citation_id'] ?? json['citationId']).toString()];
    } else {
      parsedCitationIds = const [];
    }

    final rawScore = json['entailment_score'] ??
        json['entailmentScore'] ??
        json['confidence'] ??
        json['score'] ??
        0.0;

    final rawLabel = json['label'] ?? json['nli_label'] ?? json['entailment_status'] ?? json['status'];
    final nliLabel = NLILabel.fromString(rawLabel?.toString());

    final sentenceText = (json['sentence'] ?? json['full_sentence'] ?? json['claim_text'] ?? json['text'] ?? '').toString();
    final propText = (json['proposition'] ?? json['claim_text'] ?? sentenceText).toString();

    return Claim(
      claimId: (json['claim_id'] ?? json['claimId'] ?? json['id'] ?? '').toString(),
      sentence: sentenceText.isEmpty ? propText : sentenceText,
      proposition: propText.isEmpty ? sentenceText : propText,
      citationIds: parsedCitationIds,
      entailmentScore: (rawScore as num).toDouble(),
      label: nliLabel,
      premiseChunkId: json['premise_chunk_id'] as String? ?? json['premiseChunkId'] as String? ?? json['chunk_id'] as String?,
      explanation: json['explanation'] as String? ?? json['reasoning'] as String?,
      approved: json['approved'] as bool?,
      spanStart: json['span_start'] as int? ?? json['spanStart'] as int?,
      spanEnd: json['span_end'] as int? ?? json['spanEnd'] as int?,
    );
  }

  Map<String, dynamic> toJson() => {
    'claim_id': claimId,
    'sentence': sentence,
    'proposition': proposition,
    'citation_ids': citationIds,
    'entailment_score': entailmentScore,
    'label': label.name,
    if (premiseChunkId != null) 'premise_chunk_id': premiseChunkId,
    if (explanation != null) 'explanation': explanation,
    if (approved != null) 'approved': approved,
    if (spanStart != null) 'span_start': spanStart,
    if (spanEnd != null) 'span_end': spanEnd,
  };

  Claim copyWith({
    String? claimId,
    String? sentence,
    String? proposition,
    List<String>? citationIds,
    double? entailmentScore,
    NLILabel? label,
    String? premiseChunkId,
    String? explanation,
    String? sectionId,
    bool? approved,
    int? spanStart,
    int? spanEnd,
  }) {
    return Claim(
      claimId: claimId ?? this.claimId,
      sentence: sentence ?? this.sentence,
      proposition: proposition ?? this.proposition,
      citationIds: citationIds ?? this.citationIds,
      entailmentScore: entailmentScore ?? this.entailmentScore,
      label: label ?? this.label,
      premiseChunkId: premiseChunkId ?? this.premiseChunkId,
      explanation: explanation ?? this.explanation,
      sectionId: sectionId ?? this.sectionId,
      approved: approved ?? this.approved,
      spanStart: spanStart ?? this.spanStart,
      spanEnd: spanEnd ?? this.spanEnd,
    );
  }

  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      other is Claim &&
          runtimeType == other.runtimeType &&
          claimId == other.claimId &&
          sentence == other.sentence &&
          proposition == other.proposition &&
          listEquals(citationIds, other.citationIds) &&
          entailmentScore == other.entailmentScore &&
          label == other.label &&
          premiseChunkId == other.premiseChunkId &&
          explanation == other.explanation &&
          approved == other.approved;

  @override
  int get hashCode => Object.hash(
        claimId,
        sentence,
        proposition,
        Object.hashAll(citationIds),
        entailmentScore,
        label,
        premiseChunkId,
        explanation,
        approved,
      );

  @override
  String toString() =>
      'Claim(id: $claimId, sentence: $sentence, score: $entailmentScore, label: ${label.name})';
}
