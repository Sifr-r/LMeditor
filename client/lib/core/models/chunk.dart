import 'package:flutter/foundation.dart';

/// Immutable model representing a segmented source passage with provenance metadata.
@immutable
class SourceChunk {
  final String id;
  final String documentId;
  final String content;
  final int startChar;
  final int endChar;
  final String sha256;
  final Map<String, dynamic> metadata;

  SourceChunk({
    required this.id,
    required this.documentId,
    required this.content,
    required this.startChar,
    required this.endChar,
    required this.sha256,
    this.metadata = const {},
  }) {
    if (id.trim().isEmpty) {
      throw ArgumentError.value(id, 'id', 'SourceChunk id cannot be empty');
    }
    if (documentId.trim().isEmpty) {
      throw ArgumentError.value(documentId, 'documentId', 'SourceChunk documentId cannot be empty');
    }
    if (startChar < 0) {
      throw ArgumentError.value(startChar, 'startChar', 'SourceChunk startChar cannot be negative');
    }
    if (endChar < startChar) {
      throw ArgumentError.value(endChar, 'endChar', 'SourceChunk endChar cannot precede startChar');
    }
    if (sha256.trim().isEmpty) {
      throw ArgumentError.value(sha256, 'sha256', 'SourceChunk sha256 cannot be empty');
    }
  }

  String get chunkId => id;
  String get text => content;
  String get sha256Hash => sha256;
  String get documentTitle =>
      metadata['title'] as String? ??
      metadata['document_title'] as String? ??
      metadata['sourceTitle'] as String? ??
      documentId;

  factory SourceChunk.fromJson(Map<String, dynamic> json) {
    return SourceChunk(
      id: (json['id'] ?? json['chunk_id'] ?? json['chunkId'] ?? '').toString(),
      documentId: (json['document_id'] ??
              json['documentId'] ??
              json['document_title'] ??
              json['documentTitle'] ??
              'doc-1')
          .toString(),
      content: (json['content'] ?? json['text'] ?? '').toString(),
      startChar: (json['start_char'] ?? json['startChar'] ?? 0) as int,
      endChar: (json['end_char'] ?? json['endChar'] ?? 0) as int,
      sha256: (json['sha256'] ?? json['sha256_hash'] ?? json['sha256Hash'] ?? 'hash').toString(),
      metadata: json['metadata'] is Map<String, dynamic>
          ? Map<String, dynamic>.from(json['metadata'] as Map)
          : const {},
    );
  }

  Map<String, dynamic> toJson() => {
    'id': id,
    'document_id': documentId,
    'content': content,
    'start_char': startChar,
    'end_char': endChar,
    'sha256': sha256,
    'metadata': metadata,
  };

  SourceChunk copyWith({
    String? id,
    String? documentId,
    String? content,
    int? startChar,
    int? endChar,
    String? sha256,
    Map<String, dynamic>? metadata,
  }) {
    return SourceChunk(
      id: id ?? this.id,
      documentId: documentId ?? this.documentId,
      content: content ?? this.content,
      startChar: startChar ?? this.startChar,
      endChar: endChar ?? this.endChar,
      sha256: sha256 ?? this.sha256,
      metadata: metadata ?? this.metadata,
    );
  }

  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      other is SourceChunk &&
          runtimeType == other.runtimeType &&
          id == other.id &&
          documentId == other.documentId &&
          content == other.content &&
          startChar == other.startChar &&
          endChar == other.endChar &&
          sha256 == other.sha256 &&
          mapEquals(metadata, other.metadata);

  @override
  int get hashCode => Object.hash(
        id,
        documentId,
        content,
        startChar,
        endChar,
        sha256,
      );

  @override
  String toString() =>
      'SourceChunk(id: $id, doc: $documentId, span: $startChar..$endChar, sha256: $sha256, content: $content)';
}
