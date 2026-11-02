package lmeditor.desktop.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
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
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import lmeditor.core.NotebookAnswer
import lmeditor.core.NotebookCitation
import lmeditor.core.NotebookSummary
import lmeditor.desktop.model.NotebookWorkspace
import lmeditor.desktop.model.SourceSummary
import lmeditor.desktop.model.StudioController
import lmeditor.desktop.model.StudioState

/**
 * The notebook workspace.
 *
 * A notebook is a named collection of sources and notes that lives in a folder
 * on the operator's disk, and this is the window that works inside it. The
 * operator points at the folder they keep their notebooks in, sees the
 * notebooks in it, opens one, adds the sources and notes that belong to it,
 * and asks it a question.
 *
 * The ask is the point. The answer comes only from the notebook's own
 * sources, and the passages it leaned on are cited beneath the answer. When
 * the sources cannot answer, the window says so: a refusal is shown as a
 * refusal, in its own colour and under its own label, because an answer the
 * operator cannot trust is worse than no answer at all.
 *
 * It is the same window as the studio, the documents workspace and the vault:
 * the same surfaces, the same chrome. Only the job is different.
 *
 * @param onOpenPassage opens a cited passage in the studio's source pane, so
 * the operator can read the evidence there rather than trust it here.
 */
@Composable
fun NotebookScreen(
    state: StudioState,
    controller: StudioController,
    onOpenPassage: (String) -> Unit,
    modifier: Modifier = Modifier,
) {
    val workspace = state.notebooks

    // The server always has a notebooks folder -- the root, if the operator
    // has not chosen one -- so the window can show it straight away. The
    // sources are read as well, because the notebook points at the material
    // the server already holds.
    LaunchedEffect(Unit) {
        controller.refreshNotebooks()
        controller.refreshSources()
    }

    val onChooseFolder = {
        val folder = FileDialogs.chooseFolder("Open a notebooks folder")
        if (folder != null) controller.openNotebooks(folder)
    }

    Column(modifier = modifier.fillMaxSize().background(MaterialTheme.colorScheme.background)) {
        NotebookHeader(
            state = state,
            controller = controller,
            onChooseFolder = onChooseFolder,
        )
        HorizontalDivider(color = MaterialTheme.colorScheme.outline.copy(alpha = 0.25f))

        Row(modifier = Modifier.weight(1f).fillMaxWidth()) {
            NotebookIndexColumn(
                workspace = workspace,
                controller = controller,
                modifier = Modifier.width(280.dp).fillMaxHeight(),
            )
            VerticalDivider(color = MaterialTheme.colorScheme.outline.copy(alpha = 0.25f))
            NotebookCanvas(
                state = state,
                workspace = workspace,
                controller = controller,
                onOpenPassage = onOpenPassage,
                modifier = Modifier.weight(1f).fillMaxHeight(),
            )
        }

        HorizontalDivider(color = MaterialTheme.colorScheme.outline.copy(alpha = 0.25f))
        NotebookStatusStrip(state, controller, modifier = Modifier.fillMaxWidth())
    }
}

/** The panel the header is showing, if any. */
private enum class NotebookPanel {
    /** No panel: the header shows only its actions. */
    NONE,

    /** The panel that names a new notebook and creates it. */
    NEW_NOTEBOOK,
}

/**
 * The strip across the top of the workspace.
 *
 * It carries the folder's name and path, the actions that act on the open
 * notebook, and, beside them, the one thing the operator must not have to
 * guess: whether the title and the description on screen have been written
 * back to the notebook yet.
 */
@Composable
private fun NotebookHeader(
    state: StudioState,
    controller: StudioController,
    onChooseFolder: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val workspace = state.notebooks
    var panel by remember { mutableStateOf(NotebookPanel.NONE) }
    val library = workspace.library
    val openNotebook = workspace.openNotebook

    Surface(modifier = modifier.fillMaxWidth(), color = MaterialTheme.colorScheme.surface) {
        Column {
            Row(
                modifier = Modifier.fillMaxWidth().padding(horizontal = 20.dp, vertical = 12.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                Column(Modifier.weight(1f)) {
                    Text(
                        notebookFolderName(workspace),
                        style = MaterialTheme.typography.titleMedium,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                    Text(
                        library?.root ?: "Choose a folder of notebooks to begin",
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
                            openNotebookLabel(workspace),
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                        )
                        if (workspace.dirty) UnsavedPill()
                    }
                }

                Button(
                    onClick = onChooseFolder,
                    shape = RoundedCornerShape(6.dp),
                ) {
                    Text("Choose folder…", fontWeight = FontWeight.SemiBold)
                }
                OutlinedButton(
                    onClick = { panel = NotebookPanel.NEW_NOTEBOOK },
                    shape = RoundedCornerShape(6.dp),
                ) {
                    Text("New notebook")
                }
                OutlinedButton(
                    onClick = { controller.saveNotebook() },
                    enabled = openNotebook != null && !workspace.busy,
                    shape = RoundedCornerShape(6.dp),
                ) {
                    Text("Save")
                }
                OutlinedButton(
                    onClick = { controller.deleteNotebook() },
                    enabled = openNotebook != null && !workspace.busy,
                    shape = RoundedCornerShape(6.dp),
                ) {
                    Text("Delete")
                }
            }

            when (panel) {
                NotebookPanel.NONE -> Unit

                NotebookPanel.NEW_NOTEBOOK -> NewNotebookPanel(
                    onCreate = { title, description ->
                        controller.createNotebook(title, description)
                        panel = NotebookPanel.NONE
                    },
                    onCancel = { panel = NotebookPanel.NONE },
                )
            }
        }
    }
}

/**
 * The panel that names a new notebook.
 *
 * The title is the notebook's name and the description says what it is for;
 * the server keeps both, and the notebook's own directory is named after the
 * title.
 */
@Composable
private fun NewNotebookPanel(
    onCreate: (String, String) -> Unit,
    onCancel: () -> Unit,
    modifier: Modifier = Modifier,
) {
    var title by remember { mutableStateOf("") }
    var description by remember { mutableStateOf("") }

    Row(
        modifier = modifier
            .fillMaxWidth()
            .background(MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.45f))
            .padding(horizontal = 20.dp, vertical = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        OutlinedTextField(
            value = title,
            onValueChange = { title = it },
            label = { Text("Title") },
            singleLine = true,
            modifier = Modifier.width(240.dp),
        )
        OutlinedTextField(
            value = description,
            onValueChange = { description = it },
            label = { Text("Description") },
            singleLine = true,
            modifier = Modifier.width(280.dp),
        )
        Text(
            "A notebook gathers the sources and notes of one question.",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.weight(1f),
        )
        Button(
            onClick = { onCreate(title.trim(), description.trim()) },
            enabled = title.isNotBlank(),
            shape = RoundedCornerShape(6.dp),
        ) {
            Text("Create")
        }
        TextButton(onClick = onCancel) {
            Text("Cancel")
        }
    }
}

/** The mark that says the notebook on screen is not yet the one on disk. */
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
 * The index of the notebooks.
 *
 * Every notebook of the folder, with the number of sources and the number of
 * notes it holds, so the operator can see at a glance which notebook is the
 * one they want.
 */
@Composable
private fun NotebookIndexColumn(
    workspace: NotebookWorkspace,
    controller: StudioController,
    modifier: Modifier = Modifier,
) {
    Column(
        modifier = modifier
            .fillMaxHeight()
            .background(MaterialTheme.colorScheme.surface)
            .padding(12.dp),
    ) {
        Text("Notebooks", style = MaterialTheme.typography.titleMedium)
        Text(
            notebookCountLabel(workspace),
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(top = 2.dp, bottom = 8.dp),
        )

        if (workspace.notebooks.isEmpty()) {
            Text(
                if (workspace.library == null) {
                    "No notebooks folder is open. Choose the folder you keep your notebooks in."
                } else {
                    "This folder holds no notebooks yet. Make one with New notebook."
                },
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(top = 6.dp),
            )
        } else {
            LazyColumn(modifier = Modifier.weight(1f)) {
                items(workspace.notebooks, key = { it.notebookId }) { notebook ->
                    NotebookRow(
                        notebook = notebook,
                        selected = notebook.notebookId == workspace.openNotebook?.notebookId,
                        onClick = { controller.selectNotebook(notebook.notebookId) },
                    )
                }
            }
        }
    }
}

/** One notebook of the library, in the index. */
@Composable
private fun NotebookRow(
    notebook: NotebookSummary,
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
            notebook.title.ifBlank { notebook.notebookId },
            style = MaterialTheme.typography.bodyMedium,
            color = if (selected) MaterialTheme.colorScheme.primary
            else MaterialTheme.colorScheme.onSurface,
            maxLines = 2,
            overflow = TextOverflow.Ellipsis,
        )
        Text(
            "${notebook.sourceCount} source(s) · ${notebook.noteCount} note(s)",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
        )
    }
}

/**
 * The open notebook and the ask.
 *
 * The notebook's own title and description, the sources and the notes it
 * holds, and then the question. The answer is shown beneath the question,
 * with the passages it leaned on under it.
 */
@Composable
private fun NotebookCanvas(
    state: StudioState,
    workspace: NotebookWorkspace,
    controller: StudioController,
    onOpenPassage: (String) -> Unit,
    modifier: Modifier = Modifier,
) {
    val notebook = workspace.openNotebook

    Column(
        modifier = modifier
            .fillMaxSize()
            .background(MaterialTheme.colorScheme.surfaceVariant)
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 24.dp, vertical = 20.dp),
    ) {
        if (notebook == null) {
            Text(
                "No notebook is open.",
                style = MaterialTheme.typography.titleLarge,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Spacer(Modifier.height(12.dp))
            Text(
                "Pick a notebook from the index, or make one with New notebook. " +
                    "A notebook gathers the sources and the notes that belong to one question.",
                style = MaterialTheme.typography.bodyLarge,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            return@Column
        }

        Text(
            "NOTEBOOK",
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            letterSpacing = 2.sp,
        )
        Spacer(Modifier.height(10.dp))

        OutlinedTextField(
            value = workspace.titleDraft,
            onValueChange = controller::setNotebookTitle,
            label = { Text("Title") },
            singleLine = true,
            modifier = Modifier.fillMaxWidth(),
        )
        Spacer(Modifier.height(10.dp))
        OutlinedTextField(
            value = workspace.descriptionDraft,
            onValueChange = controller::setNotebookDescription,
            label = { Text("Description") },
            minLines = 1,
            maxLines = 3,
            modifier = Modifier.fillMaxWidth(),
        )
        Spacer(Modifier.height(16.dp))

        SourceSection(
            workspace = workspace,
            sources = state.sources,
            controller = controller,
        )
        Spacer(Modifier.height(20.dp))

        NoteSection(workspace = workspace, controller = controller)
        Spacer(Modifier.height(24.dp))

        AskSection(
            workspace = workspace,
            controller = controller,
            onOpenPassage = onOpenPassage,
        )
    }
}

/**
 * The sources the notebook holds, and the ones it could hold.
 *
 * A notebook points at its sources; it does not own them. Removing one from
 * the notebook leaves the source where it is.
 */
@Composable
private fun SourceSection(
    workspace: NotebookWorkspace,
    sources: List<SourceSummary>,
    controller: StudioController,
    modifier: Modifier = Modifier,
) {
    val notebook = workspace.openNotebook ?: return
    val held = notebook.sourceIds
    val available = sources.filter { it.sourceId !in held }

    Column(modifier = modifier.fillMaxWidth()) {
        Text(
            "SOURCES (${held.size})",
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            letterSpacing = 2.sp,
        )
        Spacer(Modifier.height(6.dp))

        if (held.isEmpty()) {
            Text(
                "This notebook holds no sources yet, so it has nothing to answer from.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(top = 4.dp),
            )
        } else {
            held.forEach { sourceId ->
                HeldSourceRow(
                    title = sourceTitle(sources, sourceId),
                    sourceId = sourceId,
                    onRemove = { controller.removeNotebookSource(sourceId) },
                )
            }
        }

        Spacer(Modifier.height(10.dp))
        Text(
            "AVAILABLE SOURCES (${available.size})",
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            letterSpacing = 2.sp,
        )
        Spacer(Modifier.height(6.dp))
        if (available.isEmpty()) {
            Text(
                if (sources.isEmpty()) {
                    "The server holds no sources yet."
                } else {
                    "Every source the server holds is already in this notebook."
                },
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(top = 4.dp),
            )
        } else {
            Column(modifier = Modifier.heightIn(max = 220.dp).verticalScroll(rememberScrollState())) {
                available.forEach { source ->
                    AvailableSourceRow(
                        source = source,
                        onAdd = { controller.addNotebookSource(source.sourceId) },
                    )
                }
            }
        }
    }
}

/** One source the notebook holds, with the control that takes it out. */
@Composable
private fun HeldSourceRow(
    title: String,
    sourceId: String,
    onRemove: () -> Unit,
    modifier: Modifier = Modifier,
) {
    Row(
        modifier = modifier.fillMaxWidth().padding(vertical = 3.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        Column(Modifier.weight(1f)) {
            Text(
                title,
                style = MaterialTheme.typography.bodyMedium,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            Text(
                sourceId,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
        }
        TextButton(onClick = onRemove) {
            Text("Remove")
        }
    }
}

/** One source the server holds and the notebook does not, with the control that files it. */
@Composable
private fun AvailableSourceRow(
    source: SourceSummary,
    onAdd: () -> Unit,
    modifier: Modifier = Modifier,
) {
    Row(
        modifier = modifier.fillMaxWidth().padding(vertical = 3.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        Column(Modifier.weight(1f)) {
            Text(
                source.title.ifBlank { source.sourceId },
                style = MaterialTheme.typography.bodyMedium,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            Text(
                source.sourceId,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
        }
        TextButton(onClick = onAdd) {
            Text("Add")
        }
    }
}

/**
 * The notes the notebook refers to.
 *
 * A note is a note of the vault, named by its path, so the notebook only
 * points at it.
 */
@Composable
private fun NoteSection(
    workspace: NotebookWorkspace,
    controller: StudioController,
    modifier: Modifier = Modifier,
) {
    val notebook = workspace.openNotebook ?: return
    var noteId by remember { mutableStateOf("") }

    Column(modifier = modifier.fillMaxWidth()) {
        Text(
            "NOTES (${notebook.noteIds.size})",
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            letterSpacing = 2.sp,
        )
        Spacer(Modifier.height(6.dp))

        if (notebook.noteIds.isEmpty()) {
            Text(
                "This notebook refers to no notes yet.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(top = 4.dp),
            )
        } else {
            notebook.noteIds.forEach { heldId ->
                Row(
                    modifier = Modifier.fillMaxWidth().padding(vertical = 3.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    Text(
                        heldId,
                        style = MaterialTheme.typography.bodyMedium,
                        modifier = Modifier.weight(1f),
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                    TextButton(onClick = { controller.removeNotebookNote(heldId) }) {
                        Text("Remove")
                    }
                }
            }
        }

        Spacer(Modifier.height(8.dp))
        Row(
            modifier = Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            OutlinedTextField(
                value = noteId,
                onValueChange = { noteId = it },
                label = { Text("Note id") },
                singleLine = true,
                modifier = Modifier.weight(1f),
            )
            Button(
                onClick = {
                    controller.addNotebookNote(noteId.trim())
                    noteId = ""
                },
                enabled = noteId.isNotBlank(),
                shape = RoundedCornerShape(6.dp),
            ) {
                Text("Add note")
            }
        }
    }
}

/**
 * The ask.
 *
 * One question, answered only from the notebook's own sources, with the
 * passages it leaned on beneath the answer.
 */
@Composable
private fun AskSection(
    workspace: NotebookWorkspace,
    controller: StudioController,
    onOpenPassage: (String) -> Unit,
    modifier: Modifier = Modifier,
) {
    Column(modifier = modifier.fillMaxWidth()) {
        Text(
            "ASK",
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            letterSpacing = 2.sp,
        )
        Spacer(Modifier.height(6.dp))

        OutlinedTextField(
            value = workspace.question,
            onValueChange = controller::setNotebookQuestion,
            label = { Text("Question") },
            minLines = 1,
            maxLines = 4,
            modifier = Modifier.fillMaxWidth(),
        )
        Spacer(Modifier.height(8.dp))
        Row(
            modifier = Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Button(
                onClick = { controller.askNotebook() },
                enabled = workspace.openNotebook != null &&
                    workspace.question.isNotBlank() &&
                    !workspace.busy,
                shape = RoundedCornerShape(6.dp),
            ) {
                Text("Ask")
            }
            Text(
                "The answer comes only from this notebook's own sources.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }

        val answer = workspace.answer
        if (answer != null) {
            Spacer(Modifier.height(14.dp))
            AnswerPanel(answer = answer, onOpenPassage = onOpenPassage)
        }
    }
}

/**
 * The answer, and the passages it leaned on.
 *
 * An answer that leaned on the notebook's sources is shown as an answer, with
 * its citations under it. An answer that says the sources do not answer the
 * question is shown as a refusal: its own label, its own colour and no
 * citations, so it cannot be read as though the machine had answered.
 */
@Composable
private fun AnswerPanel(
    answer: NotebookAnswer,
    onOpenPassage: (String) -> Unit,
    modifier: Modifier = Modifier,
) {
    if (!answer.answered) {
        Column(
            modifier = modifier
                .fillMaxWidth()
                .background(
                    MaterialTheme.colorScheme.errorContainer,
                    RoundedCornerShape(6.dp),
                )
                .padding(16.dp),
        ) {
            Text(
                "NOT ANSWERED",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onErrorContainer,
                letterSpacing = 2.sp,
            )
            Spacer(Modifier.height(8.dp))
            Text(
                answer.answer.ifBlank {
                    "The notebook's own sources do not answer this question."
                },
                style = MaterialTheme.typography.bodyLarge,
                color = MaterialTheme.colorScheme.onErrorContainer,
            )
            Spacer(Modifier.height(6.dp))
            Text(
                "No passage of this notebook's sources was cited, because none of them answered.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onErrorContainer,
            )
        }
        return
    }

    Column(modifier = modifier.fillMaxWidth()) {
        Text(
            "ANSWERED FROM THIS NOTEBOOK",
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            letterSpacing = 2.sp,
        )
        Spacer(Modifier.height(8.dp))
        Text(
            answer.answer,
            style = MaterialTheme.typography.bodyLarge,
        )
        Spacer(Modifier.height(14.dp))
        Text(
            "CITATIONS (${answer.citations.size})",
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            letterSpacing = 2.sp,
        )
        Spacer(Modifier.height(6.dp))
        if (answer.citations.isEmpty()) {
            Text(
                "The answer cited no passage.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        } else {
            answer.citations.forEach { citation ->
                CitationPanel(citation = citation, onOpenPassage = onOpenPassage)
                Spacer(Modifier.height(8.dp))
            }
        }
    }
}

/**
 * One passage the answer leaned on.
 *
 * The source it belongs to, and the passage's own words: the operator reads
 * the evidence rather than trust it. The passage is clickable, and opens in
 * the studio's source pane, where the operator can read it in the company of
 * the other passages of the run.
 */
@Composable
private fun CitationPanel(
    citation: NotebookCitation,
    onOpenPassage: (String) -> Unit,
    modifier: Modifier = Modifier,
) {
    Column(
        modifier = modifier
            .fillMaxWidth()
            .background(MaterialTheme.colorScheme.surface)
            .clickable { onOpenPassage(citation.chunkId) }
            .padding(14.dp),
    ) {
        Text(
            citation.title.ifBlank { citation.sourceId },
            style = MaterialTheme.typography.titleSmall,
            maxLines = 2,
            overflow = TextOverflow.Ellipsis,
        )
        Text(
            citation.sourceId,
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
        )
        if (citation.quote.isNotBlank()) {
            Spacer(Modifier.height(8.dp))
            Text(
                "“${citation.quote}”",
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        Spacer(Modifier.height(8.dp))
        Text(
            "Open this passage in the studio",
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.primary,
        )
    }
}

/**
 * The strip under the workspace.
 *
 * It says how much the folder holds, and it carries the last failure or the
 * last thing worth telling the operator.
 */
@Composable
private fun NotebookStatusStrip(
    state: StudioState,
    controller: StudioController,
    modifier: Modifier = Modifier,
) {
    val workspace = state.notebooks

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
                if (workspace.busy) "Working" else "Notebooks",
                style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.primary,
                maxLines = 1,
            )
            Text(
                notebookCountLabel(workspace),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.weight(1f),
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            Text(
                openNotebookCounts(workspace),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
        }

        val error = workspace.error ?: state.error
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
                TextButton(
                    onClick = {
                        if (workspace.error != null) controller.clearNotebookError()
                        controller.clearError()
                    },
                ) {
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
private fun notebookFolderName(workspace: NotebookWorkspace): String {
    val library = workspace.library ?: return "No notebooks folder open"
    if (library.name.isNotBlank()) return library.name
    val root = library.root.trimEnd('/', '\\')
    return root.substringAfterLast('/').substringAfterLast('\\').ifBlank { library.root }
}

/** What the index says the folder holds. */
private fun notebookCountLabel(workspace: NotebookWorkspace): String {
    val library = workspace.library ?: return "No notebooks folder open"
    return "${library.notebookCount} notebook(s)"
}

/** What the strip says about the open notebook. */
private fun openNotebookCounts(workspace: NotebookWorkspace): String {
    val notebook = workspace.openNotebook ?: return "No notebook open"
    return "${notebook.sourceIds.size} source(s) · ${notebook.noteIds.size} note(s) in the open one"
}

/** What the header says about the open notebook. */
private fun openNotebookLabel(workspace: NotebookWorkspace): String {
    val notebook = workspace.openNotebook ?: return "No notebook open"
    return notebook.title.ifBlank { notebook.notebookId }
}

/** The title of a source, when the server knows one. */
private fun sourceTitle(sources: List<SourceSummary>, sourceId: String): String =
    sources.firstOrNull { it.sourceId == sourceId }?.title?.ifBlank { null } ?: sourceId
