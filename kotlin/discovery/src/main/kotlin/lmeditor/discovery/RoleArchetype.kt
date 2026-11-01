package lmeditor.discovery

import lmeditor.core.PerspectiveRole

/**
 * One archetype of the persona table.
 *
 * An archetype is the *stance* a perspective takes: the role it argues from,
 * the name it is known by, the description that says what it is for, the
 * focus areas it reads for, and the questions it asks.
 *
 * The substance of a persona -- what it actually says about the corpus -- is
 * derived from the material at generation time; the archetype supplies only
 * the stance.
 */
data class RoleArchetype(
    /** The role the archetype argues from. */
    val role: PerspectiveRole,

    /** The archetype's display name, without the topic. */
    val name: String,

    /** What the archetype is for, in one sentence. */
    val description: String,

    /** The aspects of the subject the archetype reads for. */
    val focusAreas: List<String>,

    /** The questions the archetype asks, `{topic}` being the subject. */
    val guidingQuestions: List<String>,
)
