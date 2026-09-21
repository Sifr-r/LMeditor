import 'package:flutter/foundation.dart';

/// Immutable model representing a STORM research persona with perspective queries.
@immutable
class Perspective {
  final String id;
  final String name;
  final String role;
  final String description;
  final List<String> focusQueries;
  final bool isSelected;
  final String avatarSymbol;

  Perspective({
    required this.id,
    required this.name,
    required this.role,
    this.description = '',
    List<String>? focusQueries,
    List<String>? focusQuestions,
    this.isSelected = true,
    String? avatarSymbol,
    String? avatar,
  })  : focusQueries = focusQueries ?? focusQuestions ?? const [],
        avatarSymbol = avatarSymbol ?? avatar ?? '🔬' {
    if (id.trim().isEmpty) {
      throw ArgumentError.value(id, 'id', 'Perspective id cannot be empty');
    }
    if (name.trim().isEmpty) {
      throw ArgumentError.value(name, 'name', 'Perspective name cannot be empty');
    }
    if (role.trim().isEmpty) {
      throw ArgumentError.value(role, 'role', 'Perspective role cannot be empty');
    }
  }

  List<String> get focusQuestions => focusQueries;
  bool get isActive => isSelected;
  String get avatar => avatarSymbol;

  factory Perspective.fromJson(Map<String, dynamic> json) {
    final rawQueries = json['focus_queries'] ??
        json['focusQueries'] ??
        json['focus_questions'] ??
        json['focusQuestions'];

    return Perspective(
      id: (json['id'] ?? '').toString(),
      name: (json['name'] ?? 'Anonymous Persona').toString(),
      role: (json['role'] ?? 'General Analyst').toString(),
      description: (json['description'] ?? '').toString(),
      focusQueries: rawQueries is List
          ? rawQueries.map((e) => e.toString()).toList()
          : const [],
      isSelected: (json['is_selected'] ?? json['isSelected'] ?? json['is_active'] ?? json['isActive'] ?? true) as bool,
      avatarSymbol: (json['avatar_symbol'] ?? json['avatarSymbol'] ?? json['avatar'] ?? '🔬').toString(),
    );
  }

  Map<String, dynamic> toJson() => {
    'id': id,
    'name': name,
    'role': role,
    'description': description,
    'focus_queries': focusQueries,
    'is_selected': isSelected,
    'avatar_symbol': avatarSymbol,
  };

  Perspective copyWith({
    String? id,
    String? name,
    String? role,
    String? description,
    List<String>? focusQueries,
    bool? isSelected,
    String? avatarSymbol,
  }) {
    return Perspective(
      id: id ?? this.id,
      name: name ?? this.name,
      role: role ?? this.role,
      description: description ?? this.description,
      focusQueries: focusQueries ?? this.focusQueries,
      isSelected: isSelected ?? this.isSelected,
      avatarSymbol: avatarSymbol ?? this.avatarSymbol,
    );
  }

  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      other is Perspective &&
          runtimeType == other.runtimeType &&
          id == other.id &&
          name == other.name &&
          role == other.role &&
          description == other.description &&
          listEquals(focusQueries, other.focusQueries) &&
          isSelected == other.isSelected &&
          avatarSymbol == other.avatarSymbol;

  @override
  int get hashCode => Object.hash(
        id,
        name,
        role,
        description,
        Object.hashAll(focusQueries),
        isSelected,
        avatarSymbol,
      );

  @override
  String toString() =>
      'Perspective(id: $id, name: $name, role: $role, queries: ${focusQueries.length}, active: $isSelected)';
}

/// Compatibility typedef for STORM persona models
typedef StormPerspective = Perspective;
