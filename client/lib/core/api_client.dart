import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'package:flutter/foundation.dart';
// ignore: unnecessary_import  -- canonical import for Uint8List (also re-exported by flutter/foundation.dart)
import 'dart:typed_data';
import 'package:http/http.dart' as http;

import '../models/source.dart';
import 'models/chunk.dart';
import 'models/citation.dart';
import 'models/claim.dart';
import 'models/outline.dart';
import 'models/perspective.dart';

// ============================================================================
// 1. Domain Exceptions
// ============================================================================

/// Abstract base domain exception for all LMeditor network and client operations.
abstract class LMeditorException implements Exception {
  final String message;
  const LMeditorException(this.message);

  @override
  String toString() => '$runtimeType: $message';
}

/// Thrown when an HTTP REST or SSE request returns a non-2xx status code.
class LMeditorApiException extends LMeditorException {
  final int statusCode;
  final String endpoint;
  final String? responseBody;

  const LMeditorApiException({
    required this.statusCode,
    required String message,
    required this.endpoint,
    this.responseBody,
  }) : super(message);

  @override
  String toString() =>
      'LMeditorApiException(statusCode: $statusCode, endpoint: $endpoint, message: $message)';
}

/// Thrown when an active SSE stream encounters a decoding, framing, or parsing failure.
class LMeditorStreamException extends LMeditorException {
  final Object? originalError;
  final StackTrace? stackTrace;

  const LMeditorStreamException({
    required String message,
    this.originalError,
    this.stackTrace,
  }) : super(message);

  @override
  String toString() =>
      'LMeditorStreamException(message: $message, originalError: $originalError)';
}

/// Thrown when the client cannot establish or maintain a socket connection with the host.
class LMeditorConnectionException extends LMeditorException {
  final String host;
  final Object? originalError;

  const LMeditorConnectionException({
    required String message,
    required this.host,
    this.originalError,
  }) : super(message);

  @override
  String toString() =>
      'LMeditorConnectionException(host: $host, message: $message)';
}

// ============================================================================
// 2. Sealed Stream Events Hierarchy
// ============================================================================

/// Sealed root for all real-time SSE events emitted by the drafting pipeline.
sealed class LMeditorStreamEvent {
  const LMeditorStreamEvent();

  /// Parse an SSE line-pair into the appropriate [LMeditorStreamEvent] subclass.
  factory LMeditorStreamEvent.parse({
    String? eventType,
    required String data,
  }) {
    return _StreamEventParser.parse(eventType: eventType, data: data);
  }
}

/// Streamed text token delta for a specific manuscript section.
final class TokenDeltaEvent extends LMeditorStreamEvent {
  final String delta;
  final String sectionId;

  const TokenDeltaEvent(this.delta, [this.sectionId = '']);

  const TokenDeltaEvent.named({
    required this.delta,
    required this.sectionId,
  });

  /// Compatibility getter for UI components expecting `text`
  String get text => delta;

  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      other is TokenDeltaEvent &&
          runtimeType == other.runtimeType &&
          delta == other.delta &&
          sectionId == other.sectionId;

  @override
  int get hashCode => Object.hash(delta, sectionId);

  @override
  String toString() => 'TokenDeltaEvent(section: $sectionId, delta: "$delta")';
}

/// Emitted when the STORM hierarchical outline tree is generated.
final class OutlineReceivedEvent extends LMeditorStreamEvent {
  final List<OutlineNode> outline;

  const OutlineReceivedEvent(this.outline);

  /// Compatibility getter for UI components expecting `nodes`
  List<OutlineNode> get nodes => outline;

  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      other is OutlineReceivedEvent &&
          runtimeType == other.runtimeType &&
          listEquals(outline, other.outline);

  @override
  int get hashCode => Object.hashAll(outline);

  @override
  String toString() => 'OutlineReceivedEvent(nodes: ${outline.length})';
}

/// Emitted when an inline token span is anchored to a source passage.
final class CitationCreatedEvent extends LMeditorStreamEvent {
  final CitationLineage citation;

  const CitationCreatedEvent(this.citation);

  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      other is CitationCreatedEvent &&
          runtimeType == other.runtimeType &&
          citation == other.citation;

  @override
  int get hashCode => citation.hashCode;

  @override
  String toString() => 'CitationCreatedEvent(citation: $citation)';
}

/// Emitted when an extracted proposition is audited via Natural Language Inference (NLI).
final class ClaimVerifiedEvent extends LMeditorStreamEvent {
  final Claim claim;

  const ClaimVerifiedEvent(this.claim);

  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      other is ClaimVerifiedEvent &&
          runtimeType == other.runtimeType &&
          claim == other.claim;

  @override
  int get hashCode => claim.hashCode;

  @override
  String toString() => 'ClaimVerifiedEvent(claim: $claim)';
}

/// Emitted when the drafting pipeline completes with overall grounding scores.
final class DoneEvent extends LMeditorStreamEvent {
  final String documentId;
  final double overallGroundingScore;

  const DoneEvent(this.documentId, this.overallGroundingScore);

  const DoneEvent.named({
    required this.documentId,
    required this.overallGroundingScore,
  });

  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      other is DoneEvent &&
          runtimeType == other.runtimeType &&
          documentId == other.documentId &&
          overallGroundingScore == other.overallGroundingScore;

  @override
  int get hashCode => Object.hash(documentId, overallGroundingScore);

  @override
  String toString() =>
      'DoneEvent(documentId: $documentId, score: $overallGroundingScore)';
}

/// Emitted when the backend encounters a streaming error or verification halt.
final class ErrorEvent extends LMeditorStreamEvent {
  final String message;
  final String? code;

  const ErrorEvent(this.message, [this.code]);

  const ErrorEvent.named({
    required this.message,
    this.code,
  });

  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      other is ErrorEvent &&
          runtimeType == other.runtimeType &&
          message == other.message &&
          code == other.code;

  @override
  int get hashCode => Object.hash(message, code);

  @override
  String toString() => 'ErrorEvent(code: $code, message: $message)';
}

// ----------------------------------------------------------------------------
// Extended Events for STORM Discovery & UI Telemetry
// ----------------------------------------------------------------------------

final class PerspectivesEvent extends LMeditorStreamEvent {
  final List<StormPerspective> perspectives;
  const PerspectivesEvent(this.perspectives);

  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      other is PerspectivesEvent &&
          runtimeType == other.runtimeType &&
          listEquals(perspectives, other.perspectives);

  @override
  int get hashCode => Object.hashAll(perspectives);

  @override
  String toString() => 'PerspectivesEvent(count: ${perspectives.length})';
}

final class ChunksEvent extends LMeditorStreamEvent {
  final List<SourceChunk> chunks;
  const ChunksEvent(this.chunks);

  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      other is ChunksEvent &&
          runtimeType == other.runtimeType &&
          listEquals(chunks, other.chunks);

  @override
  int get hashCode => Object.hashAll(chunks);

  @override
  String toString() => 'ChunksEvent(count: ${chunks.length})';
}

final class StreamMetricsEvent extends LMeditorStreamEvent {
  final int tokenCount;
  final int latencyMs;
  final double groundingScore;

  const StreamMetricsEvent({
    required this.tokenCount,
    required this.latencyMs,
    required this.groundingScore,
  });

  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      other is StreamMetricsEvent &&
          runtimeType == other.runtimeType &&
          tokenCount == other.tokenCount &&
          latencyMs == other.latencyMs &&
          groundingScore == other.groundingScore;

  @override
  int get hashCode => Object.hash(tokenCount, latencyMs, groundingScore);

  @override
  String toString() =>
      'StreamMetricsEvent(tokens: $tokenCount, latency: ${latencyMs}ms, grounding: $groundingScore)';
}

// Type aliases for UI compatibility
typedef OutlineEvent = OutlineReceivedEvent;
typedef StreamCompletedEvent = DoneEvent;
typedef SseEvent = LMeditorStreamEvent;

enum ConnectionStatus {
  connected,
  connecting,
  disconnected,
  mockMode;

  String get displayName {
    switch (this) {
      case ConnectionStatus.connected:
        return 'LIVE SSE';
      case ConnectionStatus.connecting:
        return 'CONNECTING';
      case ConnectionStatus.disconnected:
        return 'OFFLINE';
      case ConnectionStatus.mockMode:
        return 'DEMO MODE';
    }
  }
}

// ============================================================================
// 3. SSE Stream Parser Implementation
// ============================================================================

class _StreamEventParser {
  static LMeditorStreamEvent parse({
    String? eventType,
    required String data,
  }) {
    dynamic decoded;
    try {
      decoded = jsonDecode(data);
    } catch (_) {
      // If data is raw text or unquoted string, fallback heuristics
      if (eventType == 'token' || eventType == 'token_delta') {
        return TokenDeltaEvent(data, '');
      }
      return ErrorEvent('Failed to parse SSE JSON payload: $data', 'JSON_DECODE_ERROR');
    }

    final typeFromEvent = eventType?.trim().toLowerCase();
    final String? typeFromPayload = decoded is Map<String, dynamic>
        ? (decoded['event'] ?? decoded['type'])?.toString().toLowerCase()
        : null;
    final type = typeFromEvent ?? typeFromPayload;

    // Token delta event
    if (type == 'token_delta' || type == 'tokendelta' || type == 'token') {
      if (decoded is Map<String, dynamic>) {
        final delta = (decoded['delta'] ?? decoded['text'] ?? '').toString();
        final secId = (decoded['section_id'] ?? decoded['sectionId'] ?? '').toString();
        return TokenDeltaEvent(delta, secId);
      }
      return TokenDeltaEvent(decoded.toString(), '');
    }

    // Outline event
    if (type == 'outline' || type == 'outline_received' || type == 'outlinereceived') {
      final List<dynamic> list;
      if (decoded is List) {
        list = decoded;
      } else if (decoded is Map<String, dynamic>) {
        list = (decoded['outline'] ?? decoded['nodes'] ?? <dynamic>[]) as List<dynamic>;
      } else {
        list = const [];
      }
      return OutlineReceivedEvent(
        list.map((e) => OutlineNode.fromJson(e as Map<String, dynamic>)).toList(),
      );
    }

    // Perspectives event
    if (type == 'perspectives' || type == 'storm_perspectives') {
      final List<dynamic> list;
      if (decoded is List) {
        list = decoded;
      } else if (decoded is Map<String, dynamic>) {
        list = (decoded['perspectives'] ?? <dynamic>[]) as List<dynamic>;
      } else {
        list = const [];
      }
      return PerspectivesEvent(
        list.map((e) => StormPerspective.fromJson(e as Map<String, dynamic>)).toList(),
      );
    }

    // Chunks event
    if (type == 'chunks' || type == 'source_chunks') {
      final List<dynamic> list;
      if (decoded is List) {
        list = decoded;
      } else if (decoded is Map<String, dynamic>) {
        list = (decoded['chunks'] ?? <dynamic>[]) as List<dynamic>;
      } else {
        list = const [];
      }
      return ChunksEvent(
        list.map((e) => SourceChunk.fromJson(e as Map<String, dynamic>)).toList(),
      );
    }

    // Citation event
    if (type == 'citation' || type == 'citation_created' || type == 'citationcreated') {
      final Map<String, dynamic> citationMap;
      if (decoded is Map<String, dynamic>) {
        citationMap = decoded['citation'] is Map<String, dynamic>
            ? decoded['citation'] as Map<String, dynamic>
            : decoded;
      } else {
        citationMap = const {};
      }
      return CitationCreatedEvent(CitationLineage.fromJson(citationMap));
    }

    // Claim event
    if (type == 'claim' || type == 'claim_verified' || type == 'claimverified') {
      final Map<String, dynamic> claimMap;
      if (decoded is Map<String, dynamic>) {
        claimMap = decoded['claim'] is Map<String, dynamic>
            ? decoded['claim'] as Map<String, dynamic>
            : decoded;
      } else {
        claimMap = const {};
      }
      return ClaimVerifiedEvent(Claim.fromJson(claimMap));
    }

    // Stream metrics event
    if (type == 'metrics' || type == 'stream_metrics') {
      if (decoded is Map<String, dynamic>) {
        return StreamMetricsEvent(
          tokenCount: (decoded['token_count'] ?? decoded['tokenCount'] ?? 0) as int,
          latencyMs: (decoded['latency_ms'] ?? decoded['latencyMs'] ?? 0) as int,
          groundingScore: ((decoded['grounding_score'] ?? decoded['groundingScore'] ?? 1.0) as num).toDouble(),
        );
      }
    }

    // Done event
    if (type == 'done' || type == 'completed' || type == 'stream_completed') {
      if (decoded is Map<String, dynamic>) {
        final docId = (decoded['document_id'] ??
                decoded['documentId'] ??
                decoded['id'] ??
                'unspecified_doc')
            .toString();
        final score = ((decoded['overall_grounding_score'] ??
                decoded['overallGroundingScore'] ??
                decoded['grounding_score'] ??
                decoded['score'] ??
                0.0) as num)
            .toDouble();
        return DoneEvent(docId, score);
      }
      return DoneEvent('unspecified_doc', 0.0);
    }

    // Error event
    if (type == 'error') {
      if (decoded is Map<String, dynamic>) {
        final msg = (decoded['message'] ?? decoded['error'] ?? 'Unknown error').toString();
        final code = decoded['code'] as String?;
        return ErrorEvent(msg, code);
      }
      return ErrorEvent(decoded.toString());
    }

    // Fallback payload inference by inspecting JSON keys
    if (decoded is Map<String, dynamic>) {
      if (decoded.containsKey('delta')) {
        final delta = decoded['delta'].toString();
        final secId = (decoded['section_id'] ?? decoded['sectionId'] ?? '').toString();
        return TokenDeltaEvent(delta, secId);
      }
      if (decoded.containsKey('outline') || decoded.containsKey('nodes')) {
        final rawList = (decoded['outline'] ?? decoded['nodes']) as List<dynamic>? ?? const [];
        return OutlineReceivedEvent(
          rawList.map((e) => OutlineNode.fromJson(e as Map<String, dynamic>)).toList(),
        );
      }
      if (decoded.containsKey('citation_id') ||
          decoded.containsKey('citationId') ||
          decoded.containsKey('source_chunk_id') ||
          decoded.containsKey('sourceChunkId')) {
        return CitationCreatedEvent(CitationLineage.fromJson(decoded));
      }
      if (decoded.containsKey('claim_id') ||
          decoded.containsKey('claimId') ||
          decoded.containsKey('proposition') ||
          decoded.containsKey('sentence') ||
          decoded.containsKey('claim_text') ||
          decoded.containsKey('claimText')) {
        return ClaimVerifiedEvent(Claim.fromJson(decoded));
      }
      if (decoded.containsKey('overall_grounding_score') ||
          decoded.containsKey('overallGroundingScore')) {
        final docId = (decoded['document_id'] ?? decoded['documentId'] ?? 'unspecified_doc').toString();
        final score = ((decoded['overall_grounding_score'] ??
                decoded['overallGroundingScore'] ??
                0.0) as num)
            .toDouble();
        return DoneEvent(docId, score);
      }
      if (decoded.containsKey('error') || decoded.containsKey('message')) {
        final msg = (decoded['message'] ?? decoded['error']).toString();
        final code = decoded['code'] as String?;
        return ErrorEvent(msg, code);
      }
    } else if (decoded is List) {
      return OutlineReceivedEvent(
        decoded.map((e) => OutlineNode.fromJson(e as Map<String, dynamic>)).toList(),
      );
    }

    return ErrorEvent('Unrecognized event payload: $data', 'UNRECOGNIZED_EVENT');
  }
}

/// Helper function to transform a byte stream into parsed [LMeditorStreamEvent] objects.
Stream<LMeditorStreamEvent> parseSseByteStream(Stream<List<int>> byteStream) {
  return parseSseLines(
    byteStream.transform(utf8.decoder).transform(const LineSplitter()),
  );
}

/// Helper function to transform a line stream into parsed [LMeditorStreamEvent] objects.
Stream<LMeditorStreamEvent> parseSseLines(Stream<String> lineStream) async* {
  String? currentEvent;
  final dataLines = <String>[];

  await for (final rawLine in lineStream) {
    final line = rawLine.endsWith('\r')
        ? rawLine.substring(0, rawLine.length - 1)
        : rawLine;

    if (line.isEmpty) {
      if (dataLines.isNotEmpty) {
        final combined = dataLines.join('\n');
        dataLines.clear();
        yield LMeditorStreamEvent.parse(
          eventType: currentEvent,
          data: combined,
        );
        currentEvent = null;
      }
      continue;
    }

    if (line.startsWith(':')) {
      continue; // SSE comment
    }

    final colonIndex = line.indexOf(':');
    if (colonIndex == -1) {
      continue;
    }

    final field = line.substring(0, colonIndex);
    var value = line.substring(colonIndex + 1);
    if (value.startsWith(' ')) {
      value = value.substring(1);
    }

    switch (field) {
      case 'event':
        currentEvent = value;
        break;
      case 'data':
        dataLines.add(value);
        break;
      case 'id':
      case 'retry':
        break;
    }
  }

  if (dataLines.isNotEmpty) {
    yield LMeditorStreamEvent.parse(
      eventType: currentEvent,
      data: dataLines.join('\n'),
    );
  }
}

// ============================================================================
// 4. LMeditorApiClient
// ============================================================================

/// Result of `POST /api/v1/sources/ingest`.
///
/// Mirrors the backend's `SourceIngestResponse` Pydantic schema
/// (`backend/src/lmeditor/core/schemas.py`): the persisted [Source] plus the
/// list of [SourceChunk]s produced by ingestion. Chunks are returned as raw
/// `Map<String, dynamic>` to avoid a hard dependency on the chunk model from
/// this client surface; callers that need strongly-typed chunks can pass the
/// list through `SourceChunk.fromJson` (Task 15 already added those models).
class SourceUploadResult {
  final Source source;
  final List<Map<String, dynamic>> chunks;

  const SourceUploadResult({
    required this.source,
    required this.chunks,
  });

  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      other is SourceUploadResult &&
          runtimeType == other.runtimeType &&
          source == other.source &&
          listEquals(chunks, other.chunks);

  @override
  int get hashCode => Object.hash(source, Object.hashAll(chunks));

  @override
  String toString() =>
      'SourceUploadResult(source: $source, chunks: ${chunks.length})';
}

/// Primary API and SSE streaming gateway for the LMeditor writing harness.
class LMeditorApiClient {
  final String baseUrl;
  final http.Client _httpClient;
  final bool _isClientOwned;

  LMeditorApiClient({
    String baseUrl = 'http://127.0.0.1:8000',
    http.Client? client,
    http.Client? httpClient,
  })  : baseUrl = _normalizeBaseUrl(baseUrl),
        _httpClient = client ?? httpClient ?? http.Client(),
        _isClientOwned = (client == null && httpClient == null);

  static String _normalizeBaseUrl(String url) {
    var trimmed = url.trim();
    while (trimmed.endsWith('/')) {
      trimmed = trimmed.substring(0, trimmed.length - 1);
    }
    return trimmed;
  }

  void close() {
    if (_isClientOwned) {
      _httpClient.close();
    }
  }

  // --------------------------------------------------------------------------
  // Real-time SSE Stream Draft
  // --------------------------------------------------------------------------

  /// Connects to `/api/v1/draft/stream` using Server-Sent Events (SSE).
  ///
  /// Buffers multi-chunk UTF-8 data, handles line splitting, parses typed events,
  /// and supports deterministic cancellation and error handling.
  Stream<LMeditorStreamEvent> streamDraft({
    required String topic,
    List<String>? perspectives,
    String? outlineId,
    String method = 'POST',
  }) {
    late StreamController<LMeditorStreamEvent> controller;
    StreamSubscription<String>? linesSubscription;
    http.StreamedResponse? response;

    controller = StreamController<LMeditorStreamEvent>(
      onListen: () async {
        final uri = Uri.parse('$baseUrl/api/v1/draft/stream');
        final http.BaseRequest request;

        if (method.toUpperCase() == 'GET') {
          final queryParams = <String, String>{
            'topic': topic,
            if (perspectives != null && perspectives.isNotEmpty)
              'perspectives': jsonEncode(perspectives),
            'outline_id': ?outlineId,
          };
          final getUri = uri.replace(queryParameters: queryParams);
          final getReq = http.Request('GET', getUri);
          getReq.headers['Accept'] = 'text/event-stream';
          getReq.headers['Cache-Control'] = 'no-cache';
          request = getReq;
        } else {
          final postReq = http.Request('POST', uri);
          postReq.headers['Accept'] = 'text/event-stream';
          postReq.headers['Content-Type'] = 'application/json';
          postReq.headers['Cache-Control'] = 'no-cache';
          postReq.body = jsonEncode({
            'topic': topic,
            'perspectives': ?perspectives,
            'outline_id': ?outlineId,
          });
          request = postReq;
        }

        try {
          try {
            response = await _httpClient.send(request);
          } on SocketException catch (e) {
            throw LMeditorConnectionException(
              message: 'Failed to connect to host: ${e.message}',
              host: uri.host,
              originalError: e,
            );
          } on http.ClientException catch (e) {
            throw LMeditorConnectionException(
              message: 'HTTP client error: ${e.message}',
              host: uri.host,
              originalError: e,
            );
          } catch (e) {
            throw LMeditorConnectionException(
              message: 'Connection failed: $e',
              host: uri.host,
              originalError: e,
            );
          }

          if (response!.statusCode < 200 || response!.statusCode >= 300) {
            final errorBody = await response!.stream.bytesToString();
            throw LMeditorApiException(
              statusCode: response!.statusCode,
              message: 'Server returned HTTP ${response!.statusCode}: $errorBody',
              endpoint: uri.path,
              responseBody: errorBody,
            );
          }

          String? currentEvent;
          final dataLines = <String>[];

          void flushEvent() {
            if (dataLines.isEmpty) {
              currentEvent = null;
              return;
            }
            final combined = dataLines.join('\n');
            dataLines.clear();

            try {
              final parsed = LMeditorStreamEvent.parse(
                eventType: currentEvent,
                data: combined,
              );
              if (!controller.isClosed) {
                controller.add(parsed);
              }
            } catch (e) {
              if (!controller.isClosed) {
                controller.add(ErrorEvent(
                  'Failed to parse SSE event: $e',
                  'PARSE_ERROR',
                ));
              }
            }
            currentEvent = null;
          }

          linesSubscription = response!.stream
              .transform(utf8.decoder)
              .transform(const LineSplitter())
              .listen(
            (line) {
              if (line.isEmpty) {
                flushEvent();
                return;
              }
              if (line.startsWith(':')) {
                return; // Heartbeat comment
              }

              final colonIndex = line.indexOf(':');
              if (colonIndex == -1) return;

              final field = line.substring(0, colonIndex);
              var value = line.substring(colonIndex + 1);
              if (value.startsWith(' ')) {
                value = value.substring(1);
              }

              switch (field) {
                case 'event':
                  currentEvent = value;
                  break;
                case 'data':
                  dataLines.add(value);
                  break;
              }
            },
            onError: (Object error, StackTrace stackTrace) {
              if (!controller.isClosed) {
                controller.addError(
                  LMeditorStreamException(
                    message: 'SSE stream connection encountered an error: $error',
                    originalError: error,
                    stackTrace: stackTrace,
                  ),
                  stackTrace,
                );
                controller.close();
              }
            },
            onDone: () {
              flushEvent();
              if (!controller.isClosed) {
                controller.close();
              }
            },
            cancelOnError: false,
          );
        } catch (e, st) {
          if (!controller.isClosed) {
            if (e is LMeditorException) {
              controller.addError(e, st);
            } else {
              controller.addError(
                LMeditorStreamException(
                  message: 'Unexpected error during stream initialization: $e',
                  originalError: e,
                  stackTrace: st,
                ),
                st,
              );
            }
            controller.close();
          }
        }
      },
      onCancel: () async {
        await linesSubscription?.cancel();
      },
    );

    return controller.stream;
  }

  // --------------------------------------------------------------------------
  // REST Methods
  // --------------------------------------------------------------------------

  /// Generates a hierarchical outline for the given topic.
  /// POST `/api/v1/discovery/outline`
  Future<List<OutlineNode>> generateOutline(
    String topic, {
    List<String>? perspectives,
  }) async {
    final uri = Uri.parse('$baseUrl/api/v1/discovery/outline');
    final response = await _sendJsonPost(
      uri: uri,
      body: {
        'topic': topic,
        'perspectives': ?perspectives,
      },
    );

    final decoded = jsonDecode(response);
    final List<dynamic> list;
    if (decoded is List) {
      list = decoded;
    } else if (decoded is Map<String, dynamic> && decoded.containsKey('outline')) {
      list = decoded['outline'] as List<dynamic>;
    } else if (decoded is Map<String, dynamic> && decoded.containsKey('nodes')) {
      list = decoded['nodes'] as List<dynamic>;
    } else {
      throw LMeditorApiException(
        statusCode: 200,
        message: 'Unexpected response format for outline: $response',
        endpoint: uri.path,
        responseBody: response,
      );
    }

    return list.map((item) => OutlineNode.fromJson(item as Map<String, dynamic>)).toList();
  }

  /// Fetches ground-truth source passages by their chunk IDs.
  /// POST `/api/v1/provenance/chunks`
  Future<List<SourceChunk>> fetchChunks(List<String> chunkIds) async {
    final uri = Uri.parse('$baseUrl/api/v1/provenance/chunks');
    final response = await _sendJsonPost(
      uri: uri,
      body: {'chunk_ids': chunkIds},
    );

    final decoded = jsonDecode(response);
    final List<dynamic> list;
    if (decoded is List) {
      list = decoded;
    } else if (decoded is Map<String, dynamic> && decoded.containsKey('chunks')) {
      list = decoded['chunks'] as List<dynamic>;
    } else {
      throw LMeditorApiException(
        statusCode: 200,
        message: 'Unexpected response format for chunks: $response',
        endpoint: uri.path,
        responseBody: response,
      );
    }

    return list.map((item) => SourceChunk.fromJson(item as Map<String, dynamic>)).toList();
  }

  /// Submits human-in-the-loop review feedback for an individual claim.
  /// POST `/api/v1/review/feedback`
  Future<void> submitRevisionFeedback(
    String claimId,
    String feedback,
    bool approved,
  ) async {
    final uri = Uri.parse('$baseUrl/api/v1/review/feedback');
    await _sendJsonPost(
      uri: uri,
      body: {
        'claim_id': claimId,
        'feedback': feedback,
        'approved': approved,
      },
    );
  }

  /// Uploads a single source artifact (file bytes) to the ingestion pipeline.
  /// POST `/api/v1/sources/ingest` (multipart/form-data)
  ///
  /// The file is sent under the multipart field name `file`, with
  /// `source_id`, `title`, and any of `doi` / `arxiv_id` / `pmid` supplied
  /// as sibling form fields (matching the FastAPI route in
  /// `backend/src/lmeditor/server/routes.py::ingest_source`).
  ///
  /// On success (HTTP 202 Accepted), the persisted [Source] plus the list of
  /// ingested chunks are returned in a [SourceUploadResult].
  Future<SourceUploadResult> uploadSource({
    required String sourceId,
    required String title,
    required String filename,
    required Uint8List bytes,
    String contentType = 'application/octet-stream',
    String? doi,
    String? arxivId,
    String? pmid,
  }) async {
    final uri = Uri.parse('$baseUrl/api/v1/sources/ingest');
    final request = http.MultipartRequest('POST', uri)
      ..fields['source_id'] = sourceId
      ..fields['title'] = title
      ..files.add(http.MultipartFile.fromBytes(
        'file',
        bytes,
        filename: filename,
        contentType: http.MediaType.parse(contentType),
      ));

    if (doi != null) request.fields['doi'] = doi;
    if (arxivId != null) request.fields['arxiv_id'] = arxivId;
    if (pmid != null) request.fields['pmid'] = pmid;

    final http.StreamedResponse streamed;
    try {
      streamed = await _httpClient.send(request);
    } on SocketException catch (e) {
      throw LMeditorConnectionException(
        message: 'Network connection failed during source upload: ${e.message}',
        host: uri.host,
        originalError: e,
      );
    } on http.ClientException catch (e) {
      throw LMeditorConnectionException(
        message: 'HTTP client error during source upload: ${e.message}',
        host: uri.host,
        originalError: e,
      );
    } catch (e) {
      throw LMeditorConnectionException(
        message: 'Source upload request failed: $e',
        host: uri.host,
        originalError: e,
      );
    }

    final response = await http.Response.fromStream(streamed);

    if (response.statusCode < 200 || response.statusCode >= 300) {
      throw LMeditorApiException(
        statusCode: response.statusCode,
        message: 'Failed to upload source (HTTP ${response.statusCode}): ${response.body}',
        endpoint: uri.path,
        responseBody: response.body,
      );
    }

    final Map<String, dynamic> body;
    try {
      body = jsonDecode(response.body) as Map<String, dynamic>;
    } catch (e) {
      throw LMeditorApiException(
        statusCode: response.statusCode,
        message: 'Failed to decode ingest response: ${response.body}',
        endpoint: uri.path,
        responseBody: response.body,
      );
    }

    final sourceJson = body['source'];
    if (sourceJson is! Map<String, dynamic>) {
      throw LMeditorApiException(
        statusCode: response.statusCode,
        message: 'Ingest response missing "source" object: ${response.body}',
        endpoint: uri.path,
        responseBody: response.body,
      );
    }

    final rawChunks = body['chunks'];
    final chunks = rawChunks is List
        ? rawChunks
            .whereType<Map<String, dynamic>>()
            .map((c) => Map<String, dynamic>.from(c))
            .toList(growable: false)
        : const <Map<String, dynamic>>[];

    return SourceUploadResult(
      source: Source.fromJson(sourceJson),
      chunks: chunks,
    );
  }

  // --------------------------------------------------------------------------
  // High-Fidelity Mock Stream for Offline Preview & Testing
  // --------------------------------------------------------------------------

  /// Generates a realistic sequence of SSE stream events for offline preview & testing:
  /// Outline -> Perspectives -> Source Chunks -> Token deltas with citations -> Claims -> Done
  Stream<LMeditorStreamEvent> createMockStream({
    Duration stepDelay = const Duration(milliseconds: 25),
  }) async* {
    // 1. Emit STORM Outline Tree
    final mockOutline = [
      OutlineNode(
        id: 'sec-1',
        title: '1. Theoretical Formulations of Latent Dual-Encoders',
        level: 1,
        sectionGoal: 'Establish bi-encoder dot product MIPS foundations and contrastive loss optimization.',
        assignedChunkIds: const ['chunk-001', 'chunk-004'],
        perspective: 'Dense Retrieval Architect',
        isCompleted: true,
      ),
      OutlineNode(
        id: 'sec-2',
        title: '2. The Zero-Shot Out-of-Domain Generalization Paradox',
        level: 1,
        sectionGoal: 'Contrast empirical in-domain gains against lexical BM25 robustness under distribution shift.',
        assignedChunkIds: const ['chunk-002', 'chunk-003'],
        perspective: 'Critical Empirical Skeptic',
        isCompleted: false,
        subsections: [
          OutlineNode(
            id: 'sec-2-1',
            title: '2.1 Lexical Guarantees vs Latent Proximity',
            level: 2,
            sectionGoal: 'Formalize term frequency saturation versus dot product cosine metric spaces.',
            assignedChunkIds: const ['chunk-003'],
            isCompleted: false,
          ),
          OutlineNode(
            id: 'sec-2-2',
            title: '2.2 Multi-Vector Late Interaction Bridges',
            level: 2,
            sectionGoal: 'Evaluate ColBERT token-level MaxSim indexing versus single vector compression.',
            assignedChunkIds: const ['chunk-004'],
            isCompleted: false,
          ),
        ],
      ),
      OutlineNode(
        id: 'sec-3',
        title: '3. Architectural Synthesis and Verification Mandates',
        level: 1,
        sectionGoal: 'Conclude with NLI claim boundary verifications and provenance auditability.',
        assignedChunkIds: const ['chunk-001', 'chunk-002'],
        perspective: 'Ontological Taxonomist',
        isCompleted: false,
      ),
    ];
    yield OutlineReceivedEvent(mockOutline);
    if (stepDelay > Duration.zero) await Future.delayed(stepDelay);

    // 2. Emit Discovered STORM Perspectives
    final mockPerspectives = [
      StormPerspective(
        id: 'persp-1',
        name: 'Dr. Evelyn Vance',
        role: 'Dense Retrieval Architect',
        description: 'Focuses on embedding geometric spaces, dual-encoder asymmetries, and contrastive InfoNCE convergence proofs.',
        focusQuestions: [
          'How does MIPS approximate nearest-neighbor search preserve top-k semantic recall under high-dimensional manifold curvature?',
          'What are the exact dimensional bottlenecks of dense query representations against BM25 sparse baselines?',
        ],
        avatarSymbol: '📐',
      ),
      StormPerspective(
        id: 'persp-2',
        name: 'Prof. Marcus Chen',
        role: 'Critical Empirical Skeptic',
        description: 'Audits out-of-distribution drift, passage chunk truncation boundary degradation, and false premise hallucinations.',
        focusQuestions: [
          'Under what distribution shifts do bi-encoders fail to outrank BM25 in zero-shot medical and legal IR corpora?',
        ],
        avatarSymbol: '🔬',
      ),
      StormPerspective(
        id: 'persp-3',
        name: 'Elena Rostova',
        role: 'Ontological Taxonomist',
        description: 'Analyzes multi-hop reasoning graphs, entity linking precision, and citation provenance chains across heterogeneous corpora.',
        focusQuestions: [
          'Can cross-encoder re-ranking mitigate entity confusion without incurring quadratic inference latency?',
        ],
        avatarSymbol: '🏛️',
      ),
    ];
    yield PerspectivesEvent(mockPerspectives);
    if (stepDelay > Duration.zero) await Future.delayed(stepDelay);

    // 3. Emit Source Chunks
    final mockChunks = [
      SourceChunk(
        id: 'chunk-001',
        documentId: 'Karpukhin et al. (2020) - Dense Passage Retrieval for Open-Domain QA',
        content:
            'Dense Passage Retrieval (DPR) demonstrates that retrieval can be practically implemented using dense representations alone, where embeddings are learned from a small number of questions and passages by a simple dual-encoder framework. DPR out-performs strong Lucene-BM25 baselines on five open-domain QA benchmarks by 9%-19% absolute top-20 passage retrieval accuracy.',
        startChar: 104,
        endChar: 498,
        sha256: 'a7b8c9d0e1f23456789abcdef0123456789abcdef0123456789abcdef0123456',
        metadata: const {'authors': 'Karpukhin et al.', 'year': 2020, 'venue': 'EMNLP'},
      ),
      SourceChunk(
        id: 'chunk-002',
        documentId: 'Thakur et al. (2021) - BEIR: A Heterogeneous Benchmark for Zero-shot IR',
        content:
            'While dense neural retrieval models exhibit state-of-the-art performance in in-domain settings, their effectiveness degrades significantly on out-of-domain retrieval tasks. BM25 remains a formidable baseline that frequently surpasses zero-shot dense models when evaluated on specialized domain vocabularies such as BioASQ, COVID-19, and Signal-1M.',
        startChar: 45,
        endChar: 412,
        sha256: 'b8c9d0e1f2a73456789abcdef0123456789abcdef0123456789abcdef0123457',
        metadata: const {'authors': 'Thakur et al.', 'year': 2021, 'venue': 'NeurIPS'},
      ),
      SourceChunk(
        id: 'chunk-003',
        documentId: 'Robertson & Zaragoza (2009) - The Probabilistic Relevance Framework: BM25',
        content:
            'The BM25 weighting scheme balances term frequency saturation with document length normalization via parameters k1 and b. In contrast to unconstrained dot products in latent metric spaces, BM25 provides strict monotonic lexical guarantees that prevent semantic drift on exact keyword lookups.',
        startChar: 0,
        endChar: 308,
        sha256: 'c9d0e1f2a7b83456789abcdef0123456789abcdef0123456789abcdef0123458',
        metadata: const {'authors': 'Robertson & Zaragoza', 'year': 2009, 'venue': 'FTIR'},
      ),
      SourceChunk(
        id: 'chunk-004',
        documentId: 'Santhanam et al. (2022) - ColBERTv2: Effective and Efficient Late Interaction',
        content:
            'ColBERTv2 introduces residual compression of multi-vector representations, reducing index storage footprints by 6x to 10x while preserving token-level late-interaction expressiveness and outperforming single-vector dual encoders across BEIR benchmarks.',
        startChar: 112,
        endChar: 375,
        sha256: 'd0e1f2a7b8c93456789abcdef0123456789abcdef0123456789abcdef0123459',
        metadata: const {'authors': 'Santhanam et al.', 'year': 2022, 'venue': 'NAACL'},
      ),
    ];
    yield ChunksEvent(mockChunks);
    if (stepDelay > Duration.zero) await Future.delayed(stepDelay);

    // 4. Stream Token Deltas with Citations
    final textTokensSection1 = [
      '# Architectural Foundations of Dense Retrieval and Lexical Invariance\n\n',
      'Modern information retrieval paradigms have experienced an architectural bifurcation between ',
      '**probabilistic sparse inverted indices** and **dense dual-encoder vector embeddings**. ',
      'The pioneering introduction of Dense Passage Retrieval (DPR) demonstrated that top-k passage candidate generation ',
      'could be conducted purely in continuous latent Euclidean spaces ',
    ];

    for (final token in textTokensSection1) {
      yield TokenDeltaEvent(token, 'sec-1');
      if (stepDelay > Duration.zero) await Future.delayed(stepDelay);
    }

    // Emit Citation 1
    final cit1 = CitationLineage(
      citationId: 'cit-1',
      sourceChunkId: 'chunk-001',
      sectionId: 'sec-1',
      exactSpan: 'Dense Passage Retrieval (DPR) demonstrated that top-k passage candidate generation could be conducted purely in continuous latent Euclidean spaces',
      startOffset: 180,
      endOffset: 340,
      confidenceScore: 0.984,
      sourceTitle: 'Karpukhin et al. (2020)',
    );
    yield CitationCreatedEvent(cit1);
    if (stepDelay > Duration.zero) await Future.delayed(stepDelay);

    final textTokensSection2 = [
      '{{cite:cit-1:chunk-001}}, surpassing classical Lucene-BM25 baselines by up to 19% on open-domain QA benchmarks.\n\n',
      '## The Out-of-Domain Robustness Deficit\n\n',
      'However, this architectural transition introduces non-trivial failure modes when evaluating models under strict out-of-distribution domain shifts. ',
      'Comprehensive multi-domain benchmarks such as BEIR revealed that zero-shot bi-encoder performance collapses across specialized vocabularies ',
    ];

    for (final token in textTokensSection2) {
      yield TokenDeltaEvent(token, 'sec-2');
      if (stepDelay > Duration.zero) await Future.delayed(stepDelay);
    }

    // Emit Citation 2
    final cit2 = CitationLineage(
      citationId: 'cit-2',
      sourceChunkId: 'chunk-002',
      sectionId: 'sec-2',
      exactSpan: 'zero-shot bi-encoder performance collapses across specialized vocabularies',
      startOffset: 120,
      endOffset: 210,
      confidenceScore: 0.961,
      sourceTitle: 'Thakur et al. (2021)',
    );
    yield CitationCreatedEvent(cit2);
    if (stepDelay > Duration.zero) await Future.delayed(stepDelay);

    final finalTokens = [
      '{{cite:cit-2:chunk-002}}, where standard BM25 frequency weighting repeatedly outperforms deep neural encoders.\n\n',
    ];
    for (final token in finalTokens) {
      yield TokenDeltaEvent(token, 'sec-2');
      if (stepDelay > Duration.zero) await Future.delayed(stepDelay);
    }

    yield const StreamMetricsEvent(
      tokenCount: 142,
      latencyMs: 18,
      groundingScore: 0.972,
    );
    if (stepDelay > Duration.zero) await Future.delayed(stepDelay);

    // 5. Emit Verified Claims
    final mockClaims = [
      Claim(
        claimId: 'claim-001',
        sentence: 'DPR out-performs strong Lucene-BM25 baselines on five open-domain QA benchmarks by 9%-19% absolute top-20 passage retrieval accuracy.',
        proposition: 'DPR outperforms Lucene-BM25 on open-domain QA benchmarks by 9% to 19% absolute top-20 accuracy.',
        citationIds: const ['cit-1'],
        premiseChunkId: 'chunk-001',
        entailmentScore: 0.984,
        label: NLILabel.entailment,
        explanation: 'DeBERTa-v3 NLI judge confirms strict semantic entailment against Karpukhin et al. passage statistics.',
        sectionId: 'sec-1',
        spanStart: 180,
        spanEnd: 340,
        approved: true,
      ),
      Claim(
        claimId: 'claim-002',
        sentence: 'Zero-shot bi-encoder performance collapses across specialized vocabularies where BM25 repeatedly outperforms deep neural encoders.',
        proposition: 'Zero-shot dense retrieval models frequently underperform BM25 across specialized domain vocabularies in BEIR.',
        citationIds: const ['cit-2'],
        premiseChunkId: 'chunk-002',
        entailmentScore: 0.961,
        label: NLILabel.entailment,
        explanation: 'Thakur et al. benchmark results provide direct empirical grounding for zero-shot domain degradation.',
        sectionId: 'sec-2',
        spanStart: 120,
        spanEnd: 210,
        approved: true,
      ),
    ];

    for (final claim in mockClaims) {
      yield ClaimVerifiedEvent(claim);
      if (stepDelay > Duration.zero) await Future.delayed(stepDelay);
    }

    // 6. Emit Done Event
    yield const DoneEvent('doc_lmeditor_001', 0.972);
  }

  // --------------------------------------------------------------------------
  // Compatibility Methods for UI Providers
  // --------------------------------------------------------------------------

  Stream<LMeditorStreamEvent> streamGeneration({required String query}) {
    return streamDraft(topic: query);
  }

  Stream<LMeditorStreamEvent> streamDemoGeneration() {
    return createMockStream();
  }

  // --------------------------------------------------------------------------
  // HTTP POST Helper
  // --------------------------------------------------------------------------

  Future<String> _sendJsonPost({
    required Uri uri,
    required Map<String, dynamic> body,
  }) async {
    final http.Response response;
    try {
      response = await _httpClient.post(
        uri,
        headers: {
          'Content-Type': 'application/json',
          'Accept': 'application/json',
        },
        body: jsonEncode(body),
      );
    } on SocketException catch (e) {
      throw LMeditorConnectionException(
        message: 'Network connection failed: ${e.message}',
        host: uri.host,
        originalError: e,
      );
    } on http.ClientException catch (e) {
      throw LMeditorConnectionException(
        message: 'HTTP client error: ${e.message}',
        host: uri.host,
        originalError: e,
      );
    } catch (e) {
      throw LMeditorConnectionException(
        message: 'Request failed: $e',
        host: uri.host,
        originalError: e,
      );
    }

    if (response.statusCode < 200 || response.statusCode >= 300) {
      throw LMeditorApiException(
        statusCode: response.statusCode,
        message: 'API request failed with status ${response.statusCode}: ${response.body}',
        endpoint: uri.path,
        responseBody: response.body,
      );
    }

    return response.body;
  }
}

/// Backwards compatibility alias for UI providers
typedef ApiClient = LMeditorApiClient;
