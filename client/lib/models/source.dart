import 'package:flutter/foundation.dart';

/// The on-disk or on-wire kind of a source artifact to be ingested.
///
/// Mirrors the `SourceKind` enum in `backend/src/lmeditor/core/types.py`.
/// JSON wire format is the lowercase name (e.g. `pdf`, `docx`, `markdown`,
/// `html`, `text`, `url`).
enum SourceKind { pdf, docx, markdown, html, text, url }

/// Structured bibliographic metadata attached to a [Source].
///
/// Populated by the bibliography resolver chain. Required field is `title`;
/// everything else is optional and populated opportunistically by resolvers.
/// `resolver` records which resolver produced the record (e.g. `crossref`,
/// `arxiv`, `pdf-header`, or `none` for unresolved).
@immutable
class BibliographicRecord {
  final String title;
  final List<String> authors;
  final int? year;
  final String? publicationDate;
  final String? containerTitle;
  final String? doi;
  final String? url;
  final String? type;
  final String resolver;

  const BibliographicRecord({
    required this.title,
    this.authors = const [],
    this.year,
    this.publicationDate,
    this.containerTitle,
    this.doi,
    this.url,
    this.type,
    required this.resolver,
  });

  factory BibliographicRecord.fromJson(Map<String, dynamic> json) {
    return BibliographicRecord(
      title: json['title'] as String,
      authors: (json['authors'] as List?)?.cast<String>() ?? const [],
      year: json['year'] as int?,
      publicationDate: json['publication_date'] as String?,
      containerTitle: json['container_title'] as String?,
      doi: json['doi'] as String?,
      url: json['url'] as String?,
      type: json['type'] as String?,
      resolver: json['resolver'] as String,
    );
  }
}

/// A user-supplied artifact (file or URL) plus its resolved bibliographic record.
///
/// Mirrors the `Source` Pydantic model in
/// `backend/src/lmeditor/core/schemas.py`. JSON field names match the Python
/// schema exactly (`id`, `kind`, `origin`, `title`, `bibliography`,
/// `page_count`, `byte_size`, `added_at`).
@immutable
class Source {
  final String id;
  final SourceKind kind;
  final String origin;
  final String title;
  final BibliographicRecord? bibliography;
  final int? pageCount;
  final int? byteSize;
  final DateTime? addedAt;

  const Source({
    required this.id,
    required this.kind,
    required this.origin,
    required this.title,
    this.bibliography,
    this.pageCount,
    this.byteSize,
    this.addedAt,
  });

  factory Source.fromJson(Map<String, dynamic> json) {
    return Source(
      id: json['id'] as String,
      kind: _kindFromJson(json['kind'] as String),
      origin: json['origin'] as String,
      title: json['title'] as String,
      bibliography: json['bibliography'] != null
          ? BibliographicRecord.fromJson(
              json['bibliography'] as Map<String, dynamic>,
            )
          : null,
      pageCount: json['page_count'] as int?,
      byteSize: json['byte_size'] as int?,
      addedAt: json['added_at'] != null
          ? DateTime.tryParse(json['added_at'] as String)
          : null,
    );
  }
}

SourceKind _kindFromJson(String s) {
  switch (s) {
    case 'pdf':
      return SourceKind.pdf;
    case 'docx':
      return SourceKind.docx;
    case 'markdown':
      return SourceKind.markdown;
    case 'html':
      return SourceKind.html;
    case 'text':
      return SourceKind.text;
    case 'url':
      return SourceKind.url;
    default:
      throw ArgumentError('Unknown SourceKind: $s');
  }
}
