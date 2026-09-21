import 'package:flutter/foundation.dart';

/// Immutable model representing an outline section node with assigned candidate chunks.
@immutable
class OutlineNode {
  final String id;
  final String title;
  final int level;
  final String sectionGoal;
  final List<String> assignedChunkIds;
  final List<OutlineNode> subsections;
  final String? perspective;
  final bool isCompleted;

  OutlineNode({
    required this.id,
    required this.title,
    this.level = 1,
    String? sectionGoal,
    String? goal,
    this.assignedChunkIds = const [],
    this.perspective,
    List<OutlineNode>? subsections,
    List<OutlineNode>? children,
    this.isCompleted = false,
  })  : sectionGoal = sectionGoal ?? goal ?? '',
        subsections = subsections ?? children ?? const [] {
    if (id.trim().isEmpty) {
      throw ArgumentError.value(id, 'id', 'id cannot be empty');
    }
    if (title.trim().isEmpty) {
      throw ArgumentError.value(title, 'title', 'title cannot be empty');
    }
    if (level < 0) {
      throw ArgumentError.value(level, 'level', 'level cannot be negative');
    }
  }

  String get goal => sectionGoal;
  List<OutlineNode> get children => subsections;

  factory OutlineNode.fromJson(Map<String, dynamic> json) {
    final rawChunks = json['assigned_chunk_ids'] ?? json['assignedChunkIds'];
    final rawSubsections = (json['subsections'] ?? json['children']) as List<dynamic>?;

    return OutlineNode(
      id: (json['id'] ?? json['node_id'] ?? '').toString(),
      title: (json['title'] ?? '').toString(),
      level: (json['level'] ?? 1) as int,
      sectionGoal: (json['section_goal'] ?? json['sectionGoal'] ?? json['goal'] ?? '').toString(),
      assignedChunkIds: rawChunks is List
          ? rawChunks.map((e) => e.toString()).toList()
          : const [],
      subsections: rawSubsections != null
          ? rawSubsections
              .map((c) => OutlineNode.fromJson(c as Map<String, dynamic>))
              .toList()
          : const [],
      isCompleted: (json['is_completed'] ?? json['isCompleted'] ?? false) as bool,
    );
  }

  Map<String, dynamic> toJson() => {
    'id': id,
    'title': title,
    'level': level,
    'section_goal': sectionGoal,
    'assigned_chunk_ids': assignedChunkIds,
    'subsections': subsections.map((c) => c.toJson()).toList(),
    'is_completed': isCompleted,
  };

  OutlineNode copyWith({
    String? id,
    String? title,
    int? level,
    String? sectionGoal,
    List<String>? assignedChunkIds,
    List<OutlineNode>? subsections,
    bool? isCompleted,
  }) {
    return OutlineNode(
      id: id ?? this.id,
      title: title ?? this.title,
      level: level ?? this.level,
      sectionGoal: sectionGoal ?? this.sectionGoal,
      assignedChunkIds: assignedChunkIds ?? this.assignedChunkIds,
      subsections: subsections ?? this.subsections,
      isCompleted: isCompleted ?? this.isCompleted,
    );
  }

  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      other is OutlineNode &&
          runtimeType == other.runtimeType &&
          id == other.id &&
          title == other.title &&
          level == other.level &&
          sectionGoal == other.sectionGoal &&
          listEquals(assignedChunkIds, other.assignedChunkIds) &&
          listEquals(subsections, other.subsections) &&
          isCompleted == other.isCompleted;

  @override
  int get hashCode => Object.hash(
        id,
        title,
        level,
        sectionGoal,
        Object.hashAll(assignedChunkIds),
        Object.hashAll(subsections),
        isCompleted,
      );

  @override
  String toString() =>
      'OutlineNode(id: $id, title: $title, level: $level, goal: $sectionGoal, chunks: ${assignedChunkIds.length}, subsections: ${subsections.length})';
}
