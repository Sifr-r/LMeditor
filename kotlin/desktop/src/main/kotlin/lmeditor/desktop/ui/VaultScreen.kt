package lmeditor.desktop.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.VerticalDivider
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import lmeditor.core.VaultNoteSummary
import lmeditor.core.VaultSearchHit
import lmeditor.core.VaultSummary
import lmeditor.core.VaultTag
import lmeditor.desktop.model.StudioController
import lmeditor.desktop.model.StudioState
import lmeditor.desktop.model.VaultWorkspace

/**
 * The vault workspace.
 *
 * A vault is a folder of markdown files on the operator's disk, and this is
 * the window that works inside it. The operator points at the folder they
 * already keep, sees the notes in it, opens one, edits its markdown, saves
 * it back to the same file and walks the wiki-links and the tags.
 *
 * The folder stays a folder: what is written here is a `.md` file that
 * Obsidian opens and that `grep` finds. Nothing is locked inside a
 * proprietary database, so the notes are the operator's.
 *
 * It is the same window as the studio and the documents workspace: the same
 * surfaces, the same chrome, the same manuscript typography. Only the job is
 * different.
 */
@Composable
fun VaultScreen(
    state: StudioState,
    controller: StudioController,
    modifier: Modifier = Modifier,
) {
    val workspace = state.vault

    // The operator should see what the server already holds, without having
    // to open a vault first to find out that there is one. A vault that is
    // not open is not asked about: the refusal would be noise, and the
    // folder chooser is the way in.
    LaunchedEffect(Unit) {
        if (workspace.summary != null) controller.refreshVault()
    }

    val onOpenVault = {
        val folder = FileDialogs.chooseFolder("Open a vault folder")
        if (folder != null) controller.openVault(folder)
    }

    Column(modifier = modifier.fillMaxSize().background(MaterialTheme.colorScheme.background)) {
        VaultHeader(
            state = state,
            controller = controller,
            onOpenVault = onOpenVault,
        )
        HorizontalDivider(color = MaterialTheme.colorScheme.outline.copy(alpha = 0.25f))

        Row(modifier = Modifier.weight(1f).fillMaxWidth()) {
            VaultIndexColumn(
                workspace = workspace,
                controller = controller,
                modifier = Modifier.width(280.dp).fillMaxHeight(),
            )
            VerticalDivider(color = MaterialTheme.colorScheme.outline.copy(alpha = 0.25f))
            NoteCanvas(
                workspace = workspace,
                controller = controller,
                modifier = Modifier.weight(1f).fillMaxHeight(),
            )
        }

        HorizontalDivider(color = MaterialTheme.colorScheme.outline.copy(alpha = 0.25f))
        VaultStatusStrip(state, controller, modifier = Modifier.fillMaxWidth())
    }
}

/** The panel the header is showing, if any. */
private enum class VaultPanel {
    /** No panel: the header shows only its actions. */
    NONE,

    /** The panel that names a new note and creates it. */
    NEW_NOTE,

    /** The panel that gives the open note a new path. */
    RENAME,

    /** The panel that confirms the deletion of the open note. */
    DELETE,
}

/**
 * The strip across the top of the workspace.
 *
 * It carries the vault's name and path, the actions that act on the open
 * note, and, beside them, the one thing the operator must not have to
 * guess: whether the text on screen has been written back to the file yet.
 */
@Composable
private fun VaultHeader(
    state: StudioState,
    controller: StudioController,
    onOpenVault: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val workspace = state.vault
    var panel by remember { mutableStateOf(VaultPanel.NONE) }
    val summary = workspace.summary
    val openNote = workspace.openNoteId

    Surface(modifier = modifier.fillMaxWidth(), color = MaterialTheme.colorScheme.surface) {
        Column {
            Row(
                modifier = Modifier.fillMaxWidth().padding(horizontal = 20.dp, vertical = 12.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                Column(Modifier.weight(1f)) {
                    Text(
                        vaultName(summary),
                        style = MaterialTheme.typography.titleMedium,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                    Text(
                        summary?.root ?: "Choose a folder of markdown files to begin",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                    ) {
                        Text(
                            openNoteLabel(workspace),
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                        )
                        if (workspace.hasUnsavedChanges) UnsavedPill()
                    }
                }

                Button(
                    onClick = onOpenVault,
                    shape = RoundedCornerShape(6.dp),
                ) {
                    Text("Open vault…", fontWeight = FontWeight.SemiBold)
                }
                OutlinedButton(
                    onClick = { panel = VaultPanel.NEW_NOTE },
                    enabled = summary != null,
                    shape = RoundedCornerShape(6.dp),
                ) {
                    Text("New note")
                }
                OutlinedButton(
                    onClick = { controller.saveVaultNote() },
                    enabled = openNote != null && !workspace.saving,
                    shape = RoundedCornerShape(6.dp),
                ) {
                    Text(if (workspace.saving) "Saving…" else "Save")
                }
                OutlinedButton(
                    onClick = { panel = VaultPanel.RENAME },
                    enabled = openNote != null,
                    shape = RoundedCornerShape(6.dp),
                ) {
                    Text("Rename")
                }
                OutlinedButton(
                    onClick = { panel = VaultPanel.DELETE },
                    enabled = openNote != null,
                    shape = RoundedCornerShape(6.dp),
                ) {
                    Text("Delete")
                }
            }

            when (panel) {
                VaultPanel.NONE -> Unit

                VaultPanel.NEW_NOTE -> NewNotePanel(
                    onCreate = { noteId, title ->
                        controller.createVaultNote(noteId, title)
                        panel = VaultPanel.NONE
                    },
                    onCancel = { panel = VaultPanel.NONE },
                )

                VaultPanel.RENAME -> RenamePanel(
                    noteId = openNote.orEmpty(),
                    onRename = { newId ->
                        controller.renameVaultNote(openNote.orEmpty(), newId)
                        panel = VaultPanel.NONE
                    },
                    onCancel = { panel = VaultPanel.NONE },
                )

                VaultPanel.DELETE -> DeletePanel(
                    noteId = openNote.orEmpty(),
                    onDelete = {
                        controller.deleteVaultNote(openNote.orEmpty())
                        panel = VaultPanel.NONE
                    },
                    onCancel = { panel = VaultPanel.NONE },
                )
            }
        }
    }
}

/**
 * The panel that names a new note.
 *
 * The id is the note's path inside the vault, without the `.md`; the title
 * is the heading the server writes as the note's first line.
 */
@Composable
private fun NewNotePanel(
    onCreate: (String, String) -> Unit,
    onCancel: () -> Unit,
    modifier: Modifier = Modifier,
) {
    var noteId by remember { mutableStateOf("") }
    var title by remember { mutableStateOf("") }

    Row(
        modifier = modifier
            .fillMaxWidth()
            .background(MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.45f))
            .padding(horizontal = 20.dp, vertical = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        OutlinedTextField(
            value = noteId,
            onValueChange = { noteId = it },
            label = { Text("Note id") },
            singleLine = true,
            modifier = Modifier.width(240.dp),
        )
        OutlinedTextField(
            value = title,
            onValueChange = { title = it },
            label = { Text("Title") },
            singleLine = true,
            modifier = Modifier.width(240.dp),
        )
        Text(
            "The id is the note's path inside the vault, without the .md.",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.weight(1f),
        )
        Button(
            onClick = { onCreate(noteId.trim(), title.trim()) },
            enabled = noteId.isNotBlank(),
            shape = RoundedCornerShape(6.dp),
        ) {
            Text("Create")
        }
        TextButton(onClick = onCancel) {
            Text("Cancel")
        }
    }
}

/**
 * The panel that gives the open note a new path.
 *
 * The links that point at the note are rewritten to the new name, so the
 * graph stays whole; the operator is told that before it happens.
 */
@Composable
private fun RenamePanel(
    noteId: String,
    onRename: (String) -> Unit,
    onCancel: () -> Unit,
    modifier: Modifier = Modifier,
) {
    var newId by remember(noteId) { mutableStateOf(noteId) }

    Row(
        modifier = modifier
            .fillMaxWidth()
            .background(MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.45f))
            .padding(horizontal = 20.dp, vertical = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        OutlinedTextField(
            value = newId,
            onValueChange = { newId = it },
            label = { Text("New id") },
            singleLine = true,
            modifier = Modifier.width(280.dp),
        )
        Text(
            "The links that point at this note are rewritten to the new name.",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.weight(1f),
        )
        Button(
            onClick = { onRename(newId.trim()) },
            enabled = newId.isNotBlank() && newId.trim() != noteId,
            shape = RoundedCornerShape(6.dp),
        ) {
            Text("Rename")
        }
        TextButton(onClick = onCancel) {
            Text("Cancel")
        }
    }
}

/**
 * The panel that confirms the deletion of the open note.
 *
 * The note is a file on the operator's disk, so deleting it is asked for
 * before it is done.
 */
@Composable
private fun DeletePanel(
    noteId: String,
    onDelete: () -> Unit,
    onCancel: () -> Unit,
    modifier: Modifier = Modifier,
) {
    Row(
        modifier = modifier
            .fillMaxWidth()
            .background(MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.45f))
            .padding(horizontal = 20.dp, vertical = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text(
            "Delete $noteId? The file is removed from the vault folder.",
            style = MaterialTheme.typography.bodyMedium,
            modifier = Modifier.weight(1f),
        )
        Button(
            onClick = onDelete,
            shape = RoundedCornerShape(6.dp),
        ) {
            Text("Delete", fontWeight = FontWeight.SemiBold)
        }
        TextButton(onClick = onCancel) {
            Text("Cancel")
        }
    }
}

/** The mark that says the note on screen is not yet the file on disk. */
@Composable
private fun UnsavedPill(modifier: Modifier = Modifier) {
    Box(
        modifier = modifier
            .background(
                MaterialTheme.colorScheme.tertiary.copy(alpha = 0.16f),
                RoundedCornerShape(10.dp),
            )
            .padding(horizontal = 10.dp, vertical = 3.dp),
    ) {
        Text(
            "Unsaved",
            style = MaterialTheme.typography.labelMedium,
            color = MaterialTheme.colorScheme.tertiary,
        )
    }
}

/**
 * The index of the vault.
 *
 * A search box at the top, then either the hits of the search or the whole
 * vault grouped by the folder the notes sit in, and under that the tags of
 * the vault with the number of notes that carry them. A note is opened on
 * its own row; a tag is a filter, so clicking one searches the vault for it.
 */
@Composable
private fun VaultIndexColumn(
    workspace: VaultWorkspace,
    controller: StudioController,
    modifier: Modifier = Modifier,
) {
    var searchDraft by remember(workspace.searchQuery) { mutableStateOf(workspace.searchQuery) }
    val searching = workspace.searchQuery.isNotBlank()

    Column(
        modifier = modifier
            .fillMaxHeight()
            .background(MaterialTheme.colorScheme.surface)
            .padding(12.dp),
    ) {
        Text("Vault", style = MaterialTheme.typography.titleMedium)
        Text(
            vaultCountLabel(workspace),
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(top = 2.dp, bottom = 8.dp),
        )

        OutlinedTextField(
            value = searchDraft,
            onValueChange = { searchDraft = it },
            label = { Text("Search the vault") },
            singleLine = true,
            modifier = Modifier.fillMaxWidth(),
        )
        Row(
            modifier = Modifier.fillMaxWidth().padding(top = 6.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            OutlinedButton(
                onClick = { controller.searchVault(searchDraft) },
                enabled = searchDraft.isNotBlank(),
                shape = RoundedCornerShape(6.dp),
            ) {
                Text("Search")
            }
            TextButton(
                onClick = {
                    searchDraft = ""
                    controller.clearVaultSearch()
                },
                enabled = searching,
            ) {
                Text("Clear")
            }
        }

        HorizontalDivider(
            modifier = Modifier.padding(vertical = 8.dp),
            color = MaterialTheme.colorScheme.outline.copy(alpha = 0.25f),
        )

        if (searching) {
            Text(
                "Results (${workspace.results.size})",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(bottom = 4.dp),
            )
            if (workspace.results.isEmpty()) {
                Text(
                    "Nothing in this vault matches \"${workspace.searchQuery}\".",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(top = 6.dp),
                )
            } else {
                LazyColumn(modifier = Modifier.weight(1f)) {
                    items(workspace.results, key = { it.noteId }) { hit ->
                        SearchHitRow(hit = hit, onOpen = { controller.openVaultNote(hit.noteId) })
                    }
                }
            }
        } else {
            if (workspace.notes.isEmpty()) {
                Text(
                    if (workspace.summary == null) {
                        "No vault is open. Choose a folder of markdown files to begin."
                    } else {
                        "This vault holds no notes yet. Make one with New note."
                    },
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(top = 6.dp),
                )
            } else {
                val folders = workspace.notes.groupBy { folderOf(it.noteId) }
                LazyColumn(modifier = Modifier.weight(1f)) {
                    folders.forEach { (folder, notes) ->
                        item(key = "folder:$folder") {
                            Text(
                                folder.ifBlank { "Vault root" },
                                style = MaterialTheme.typography.labelMedium,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                                modifier = Modifier.padding(top = 8.dp, bottom = 2.dp),
                            )
                        }
                        items(notes, key = { it.noteId }) { note ->
                            NoteRow(
                                note = note,
                                selected = note.noteId == workspace.openNoteId,
                                onClick = { controller.openVaultNote(note.noteId) },
                            )
                        }
                    }
                }
            }
        }

        HorizontalDivider(
            modifier = Modifier.padding(vertical = 8.dp),
            color = MaterialTheme.colorScheme.outline.copy(alpha = 0.25f),
        )

        Text("Tags", style = MaterialTheme.typography.titleMedium)
        if (workspace.tags.isEmpty()) {
            Text(
                "No tags in this vault yet.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(top = 2.dp),
            )
        } else {
            LazyColumn(modifier = Modifier.heightIn(max = 170.dp).padding(top = 4.dp)) {
                items(workspace.tags, key = { it.tag }) { tag ->
                    TagRow(tag = tag, onClick = { controller.searchVault(tag.tag) })
                }
            }
        }
    }
}

/** One note of the vault, in the index. */
@Composable
private fun NoteRow(
    note: VaultNoteSummary,
    selected: Boolean,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
) {
    Column(
        modifier = modifier
            .fillMaxWidth()
            .background(
                if (selected) MaterialTheme.colorScheme.primary.copy(alpha = 0.08f)
                else MaterialTheme.colorScheme.surface,
            )
            .clickable(onClick = onClick)
            .padding(horizontal = 6.dp, vertical = 5.dp),
    ) {
        Text(
            note.title.ifBlank { note.noteId },
            style = MaterialTheme.typography.bodyMedium,
            color = if (selected) MaterialTheme.colorScheme.primary
            else MaterialTheme.colorScheme.onSurface,
            maxLines = 2,
            overflow = TextOverflow.Ellipsis,
        )
        Text(
            "${note.wordCount} w" +
                if (note.tags.isEmpty()) "" else " · ${note.tags.joinToString(" ")}",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
        )
    }
}

/** One hit of the search, with the score and the terms it matched on. */
@Composable
private fun SearchHitRow(
    hit: VaultSearchHit,
    onOpen: () -> Unit,
    modifier: Modifier = Modifier,
) {
    Column(
        modifier = modifier
            .fillMaxWidth()
            .clickable(onClick = onOpen)
            .padding(horizontal = 6.dp, vertical = 5.dp),
    ) {
        Text(
            hit.title.ifBlank { hit.noteId },
            style = MaterialTheme.typography.bodyMedium,
            maxLines = 2,
            overflow = TextOverflow.Ellipsis,
        )
        Text(
            "score ${hit.score}" +
                if (hit.matchedTerms.isEmpty()) "" else " · ${hit.matchedTerms.joinToString(" ")}",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
        )
    }
}

/** One tag of the vault, with the number of notes that carry it. */
@Composable
private fun TagRow(
    tag: VaultTag,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
) {
    Row(
        modifier = modifier
            .fillMaxWidth()
            .clickable(onClick = onClick)
            .padding(horizontal = 6.dp, vertical = 3.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            "#${tag.tag}",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.primary,
            modifier = Modifier.weight(1f),
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
        )
        Text(
            "${tag.count}",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

/**
 * The note on screen.
 *
 * It is the studio's paper: a bounded sheet on the warm surface, the
 * manuscript's serif body type, the same ruler. The difference is that the
 * body is editable -- the text is the operator's, and only Save writes it
 * back to the file it came from.
 */
@Composable
private fun NoteCanvas(
    workspace: VaultWorkspace,
    controller: StudioController,
    modifier: Modifier = Modifier,
) {
    Column(
        modifier = modifier
            .fillMaxSize()
            .background(MaterialTheme.colorScheme.surfaceVariant),
    ) {
        BoxWithConstraints(modifier = Modifier.weight(1f).fillMaxWidth()) {
            val outerMargin = if (maxWidth < 500.dp) 12.dp else 28.dp
            val paperMargin = if (maxWidth < 500.dp) 24.dp else 56.dp
            Column(
                modifier = Modifier
                    .fillMaxSize()
                    .verticalScroll(rememberScrollState())
                    .padding(horizontal = outerMargin, vertical = 16.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
            ) {
                DocumentRuler(Modifier.widthIn(max = 800.dp).fillMaxWidth(), paperMargin)
                Spacer(Modifier.height(12.dp))
                Column(
                    modifier = Modifier
                        .widthIn(max = 800.dp)
                        .fillMaxWidth()
                        .shadow(3.dp, RoundedCornerShape(1.dp))
                        .background(MaterialTheme.colorScheme.surface)
                        .border(1.dp, MaterialTheme.colorScheme.outline.copy(alpha = 0.22f))
                        .heightIn(min = 640.dp)
                        .padding(horizontal = paperMargin, vertical = 48.dp),
                ) {
                    when {
                        workspace.summary == null -> {
                            Text(
                                "No vault is open.",
                                style = MaterialTheme.typography.titleLarge,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                            Spacer(Modifier.height(12.dp))
                            Text(
                                "Open the folder of markdown files you already keep. " +
                                    "It stays a folder: Obsidian opens it, and so does an editor.",
                                style = MaterialTheme.typography.bodyLarge,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }

                        workspace.openNoteId == null -> {
                            Text(
                                "No note is open.",
                                style = MaterialTheme.typography.titleLarge,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                            Spacer(Modifier.height(12.dp))
                            Text(
                                "Pick a note from the index to read and edit it.",
                                style = MaterialTheme.typography.bodyLarge,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }

                        else -> {
                            Text(
                                "NOTE",
                                style = MaterialTheme.typography.labelSmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                                letterSpacing = 2.sp,
                            )
                            Spacer(Modifier.height(10.dp))
                            Text(
                                workspace.note?.title?.ifBlank { workspace.openNoteId }
                                    ?: workspace.openNoteId.orEmpty(),
                                style = MaterialTheme.typography.titleLarge,
                            )
                            Spacer(Modifier.height(22.dp))
                            OutlinedTextField(
                                value = workspace.draft,
                                onValueChange = controller::setVaultDraft,
                                textStyle = MaterialTheme.typography.bodyLarge.copy(
                                    color = MaterialTheme.colorScheme.onSurface,
                                ),
                                placeholder = {
                                    Text(
                                        "This note is empty. Write it here.",
                                        style = MaterialTheme.typography.bodyLarge,
                                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                                    )
                                },
                                // The field sits on the paper, so it draws no frame
                                // of its own: the canvas is the frame.
                                colors = OutlinedTextFieldDefaults.colors(
                                    focusedBorderColor = Color.Transparent,
                                    unfocusedBorderColor = Color.Transparent,
                                    focusedContainerColor = Color.Transparent,
                                    unfocusedContainerColor = Color.Transparent,
                                ),
                                minLines = 14,
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .heightIn(min = 420.dp),
                            )
                        }
                    }
                }
                Spacer(Modifier.height(18.dp))
                Text(
                    "Note text · Save writes this file back to the vault",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }

        NoteGraph(workspace = workspace, controller = controller)
    }
}

/** One entry of the note's graph: what it says, and where it goes. */
private data class GraphItem(val label: String, val target: String?)

/**
 * The graph around the open note.
 *
 * The tags the note carries, the links it holds and the backlinks that
 * point at it, so the operator can walk the vault without leaving the note.
 * An entry that leads nowhere is shown as text: a link to a note that does
 * not exist yet is still a link, but there is nothing to open.
 */
@Composable
private fun NoteGraph(
    workspace: VaultWorkspace,
    controller: StudioController,
    modifier: Modifier = Modifier,
) {
    val note = workspace.note

    val tags = note?.tags.orEmpty().map { tag -> GraphItem("#$tag", tag) }
    val links = note?.links.orEmpty().map { link ->
        val target = resolveLink(workspace, link)
        GraphItem(link, target?.noteId)
    }
    val backlinks = note?.backlinks.orEmpty().map { backlink ->
        val known = workspace.notes.any { it.noteId == backlink }
        GraphItem(backlink, if (known) backlink else null)
    }

    Row(
        modifier = modifier
            .fillMaxWidth()
            .background(MaterialTheme.colorScheme.surface)
            .padding(horizontal = 20.dp, vertical = 12.dp),
        horizontalArrangement = Arrangement.spacedBy(28.dp),
    ) {
        GraphColumn(
            heading = "TAGS",
            items = tags,
            onOpen = { item -> item.target?.let { controller.searchVault(it) } },
            modifier = Modifier.weight(1f),
        )
        GraphColumn(
            heading = "LINKS",
            items = links,
            onOpen = { item -> item.target?.let { controller.openVaultNote(it) } },
            modifier = Modifier.weight(1f),
        )
        GraphColumn(
            heading = "BACKLINKS",
            items = backlinks,
            onOpen = { item -> item.target?.let { controller.openVaultNote(it) } },
            modifier = Modifier.weight(1f),
        )
    }
}

/** One column of the graph: its heading and the entries under it. */
@Composable
private fun GraphColumn(
    heading: String,
    items: List<GraphItem>,
    onOpen: (GraphItem) -> Unit,
    modifier: Modifier = Modifier,
) {
    Column(modifier = modifier) {
        Text(
            heading,
            style = MaterialTheme.typography.labelMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Spacer(Modifier.height(4.dp))
        if (items.isEmpty()) {
            Text(
                "None",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        } else {
            for (item in items) {
                Text(
                    item.label,
                    style = MaterialTheme.typography.bodySmall,
                    color = if (item.target != null) MaterialTheme.colorScheme.primary
                    else MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = if (item.target != null) {
                        Modifier.clickable { onOpen(item) }.padding(vertical = 1.dp)
                    } else {
                        Modifier.padding(vertical = 1.dp)
                    },
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
            }
        }
    }
}

/**
 * The strip along the bottom.
 *
 * It carries the same things the studio's own strip carries: what the
 * workspace is doing, and what went wrong. A refused request must never
 * look like an empty vault.
 */
@Composable
private fun VaultStatusStrip(
    state: StudioState,
    controller: StudioController,
    modifier: Modifier = Modifier,
) {
    val workspace = state.vault

    Column(
        modifier = modifier
            .background(MaterialTheme.colorScheme.surface)
            .padding(horizontal = 18.dp, vertical = 7.dp),
    ) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(16.dp),
        ) {
            Text(
                if (workspace.saving) "Saving" else "Vault",
                style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.primary,
                maxLines = 1,
            )
            Text(
                vaultCountLabel(workspace),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.weight(1f),
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
        }

        val error = state.error
        if (error != null) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(10.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    error,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.error,
                    modifier = Modifier.weight(1f),
                )
                TextButton(onClick = controller::clearError) {
                    Text("Dismiss")
                }
            }
        }
        state.statusNotices.forEach { notice ->
            Text(
                notice,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

/** The folder's own name, whether the server named it or the path does. */
private fun vaultName(summary: VaultSummary?): String {
    if (summary == null) return "No vault open"
    if (summary.name.isNotBlank()) return summary.name
    val root = summary.root.trimEnd('/', '\\')
    return root.substringAfterLast('/').substringAfterLast('\\').ifBlank { summary.root }
}

/** What the index says the vault holds. */
private fun vaultCountLabel(workspace: VaultWorkspace): String {
    if (workspace.summary == null) return "No vault open"
    return "${workspace.notes.size} note(s) · ${workspace.tags.size} tag(s)"
}

/** What the header says about the open note. */
private fun openNoteLabel(workspace: VaultWorkspace): String {
    val noteId = workspace.openNoteId ?: return "No note open"
    val title = workspace.note?.title?.ifBlank { null }
    return title ?: noteId
}

/** The folder a note sits in: the part of its id before the last slash. */
private fun folderOf(noteId: String): String = noteId.substringBeforeLast('/', "")

/**
 * The note a wiki-link points at, when the vault holds it.
 *
 * A link is written by its title, so it is resolved against the titles of
 * the notes and against their paths, the way the tier resolves it.
 */
private fun resolveLink(workspace: VaultWorkspace, link: String): VaultNoteSummary? {
    val wanted = link.trim()
    if (wanted.isEmpty()) return null
    return workspace.notes.firstOrNull { it.title.equals(wanted, ignoreCase = true) }
        ?: workspace.notes.firstOrNull { it.noteId.equals(wanted, ignoreCase = true) }
}
