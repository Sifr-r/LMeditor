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
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import lmeditor.core.BookManifest
import lmeditor.core.BookSection
import lmeditor.desktop.model.DocumentWorkspace
import lmeditor.desktop.model.StudioController
import lmeditor.desktop.model.StudioState

/**
 * The documents workspace.
 *
 * The backend already reads a Word file into a book of editable sections and
 * writes it back out; this is the window that reaches that work. The operator
 * opens a `.docx`, reads it in the paper canvas, edits the section in front of
 * them, saves it, and writes the whole book back to disk.
 *
 * It is the same window as the studio: the same surfaces, the same chrome,
 * the same manuscript typography. Only the job is different.
 */
@Composable
fun DocumentsScreen(
    state: StudioState,
    controller: StudioController,
    modifier: Modifier = Modifier,
) {
    val workspace = state.documents

    // The operator should see what the server already holds, without having to
    // open a book first to find out that there is one.
    LaunchedEffect(Unit) {
        controller.refreshBooks()
    }

    val onOpenDocx = {
        val file = FileDialogs.chooseFileToOpen("Open a Word document")
        if (file != null) controller.openDocx(file)
    }

    val onSaveAsDocx = {
        val book = workspace.openBook
        if (book != null) {
            val target = FileDialogs.chooseSaveTarget(
                title = "Write the book as a Word document",
                suggestedName = suggestedFileName(book.title),
            )
            if (target != null) controller.exportBookDocxTo(target)
        }
    }

    Column(modifier = modifier.fillMaxSize().background(MaterialTheme.colorScheme.background)) {
        DocumentsHeader(
            state = state,
            controller = controller,
            onOpenDocx = onOpenDocx,
            onSaveAsDocx = onSaveAsDocx,
        )
        HorizontalDivider(color = MaterialTheme.colorScheme.outline.copy(alpha = 0.25f))

        Row(modifier = Modifier.weight(1f).fillMaxWidth()) {
            LibraryColumn(
                workspace = workspace,
                controller = controller,
                modifier = Modifier.width(280.dp).fillMaxHeight(),
            )
            VerticalDivider(color = MaterialTheme.colorScheme.outline.copy(alpha = 0.25f))
            SectionEditor(
                workspace = workspace,
                controller = controller,
                modifier = Modifier.weight(1f).fillMaxHeight(),
            )
        }

        HorizontalDivider(color = MaterialTheme.colorScheme.outline.copy(alpha = 0.25f))
        DocumentsStatusStrip(state, controller, modifier = Modifier.fillMaxWidth())
    }
}

/**
 * The strip across the top of the workspace.
 *
 * It carries the document's actions and, beside them, the two things the
 * operator must not have to guess: which book is open, and whether the
 * section in front of them has been written back yet.
 */
@Composable
private fun DocumentsHeader(
    state: StudioState,
    controller: StudioController,
    onOpenDocx: () -> Unit,
    onSaveAsDocx: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val workspace = state.documents
    var connectionVisible by remember { mutableStateOf(false) }
    var backendDraft by remember(state.backendUrl) { mutableStateOf(state.backendUrl) }
    val book = workspace.openBook
    val bookTitle = book?.let { it.title.ifBlank { it.bookId } } ?: "No document open"
    val sectionLabel = when {
        workspace.openSectionId != null -> workspace.sectionTitle.ifBlank { "Untitled section" }
        book != null -> "Choose a section to edit"
        else -> "Open a .docx to begin"
    }

    Surface(modifier = modifier.fillMaxWidth(), color = MaterialTheme.colorScheme.surface) {
        Column {
            Row(
                modifier = Modifier.fillMaxWidth().padding(horizontal = 20.dp, vertical = 12.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                Column(Modifier.weight(1f)) {
                    Text(
                        bookTitle,
                        style = MaterialTheme.typography.titleMedium,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                    ) {
                        Text(
                            sectionLabel,
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                        )
                        if (workspace.hasUnsavedChanges) UnsavedPill()
                    }
                }

                Button(
                    onClick = onOpenDocx,
                    shape = RoundedCornerShape(6.dp),
                ) {
                    Text("Open .docx", fontWeight = FontWeight.SemiBold)
                }
                OutlinedButton(
                    onClick = { controller.saveSection() },
                    enabled = workspace.openSectionId != null && !workspace.saving,
                    shape = RoundedCornerShape(6.dp),
                ) {
                    Text(if (workspace.saving) "Saving…" else "Save")
                }
                OutlinedButton(
                    onClick = onSaveAsDocx,
                    enabled = book != null,
                    shape = RoundedCornerShape(6.dp),
                ) {
                    Text("Save as .docx")
                }
                TextButton(
                    onClick = { controller.closeBook() },
                    enabled = book != null,
                ) {
                    Text("Close")
                }
                TextButton(onClick = { connectionVisible = !connectionVisible }) {
                    Text(if (connectionVisible) "Hide server" else "Server")
                }
            }

            if (connectionVisible) {
                Column(
                    modifier = Modifier.fillMaxWidth()
                        .background(MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.45f))
                        .padding(horizontal = 20.dp, vertical = 12.dp),
                ) {
                    BackendAddressField(
                        state = state,
                        controller = controller,
                        draft = backendDraft,
                        onDraftChange = { backendDraft = it },
                    )
                }
            }
        }
    }
}

/** The mark that says the section on screen is not yet the server's copy. */
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
 * The library.
 *
 * Every book the server holds, and, under each one, the sections it is made
 * of. A book is opened on its title and expanded on its marker; a section is
 * opened on its own row.
 */
@Composable
private fun LibraryColumn(
    workspace: DocumentWorkspace,
    controller: StudioController,
    modifier: Modifier = Modifier,
) {
    var expanded by remember { mutableStateOf(setOf<String>()) }

    Column(
        modifier = modifier
            .fillMaxHeight()
            .background(MaterialTheme.colorScheme.surface)
            .padding(12.dp),
    ) {
        Text("Library", style = MaterialTheme.typography.titleMedium)
        Text(
            "Books on the server (${workspace.books.size})",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(top = 2.dp, bottom = 8.dp),
        )

        if (workspace.books.isEmpty()) {
            Text(
                "No books on the server yet. Open a .docx to add one.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(top = 6.dp),
            )
            return@Column
        }

        LazyColumn(modifier = Modifier.weight(1f).padding(top = 4.dp)) {
            items(workspace.books, key = { it.bookId }) { book ->
                BookRow(
                    book = book,
                    expanded = book.bookId in expanded,
                    isOpen = book.bookId == workspace.openBook?.bookId,
                    openSectionId = workspace.openSectionId,
                    onToggle = {
                        expanded = if (book.bookId in expanded) expanded - book.bookId
                        else expanded + book.bookId
                    },
                    onOpenBook = {
                        expanded = expanded + book.bookId
                        controller.openBook(book.bookId)
                    },
                    onOpenSection = { controller.openSection(it) },
                )
            }
        }
    }
}

/** One book, and the sections it holds when it is expanded. */
@Composable
private fun BookRow(
    book: BookManifest,
    expanded: Boolean,
    isOpen: Boolean,
    openSectionId: String?,
    onToggle: () -> Unit,
    onOpenBook: () -> Unit,
    onOpenSection: (String) -> Unit,
    modifier: Modifier = Modifier,
) {
    Column(modifier = modifier.fillMaxWidth()) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .background(
                    if (isOpen) MaterialTheme.colorScheme.primary.copy(alpha = 0.08f)
                    else MaterialTheme.colorScheme.surface,
                )
                .clickable(onClick = onOpenBook)
                .padding(vertical = 5.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                if (expanded) "▾" else "▸",
                style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier
                    .width(24.dp)
                    .clickable(onClick = onToggle)
                    .padding(vertical = 2.dp),
            )
            Column(Modifier.weight(1f)) {
                Text(
                    book.title.ifBlank { book.bookId },
                    style = MaterialTheme.typography.bodyMedium,
                    color = if (isOpen) MaterialTheme.colorScheme.primary
                    else MaterialTheme.colorScheme.onSurface,
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis,
                )
                Text(
                    "${book.sectionCount} section(s) · ${book.chunkCount} passage(s)",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }

        if (expanded) {
            if (book.sections.isEmpty()) {
                Text(
                    "No sections listed for this book.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(start = 24.dp, top = 2.dp, bottom = 4.dp),
                )
            }
            for (section in book.sections) {
                SectionRow(
                    section = section,
                    selected = section.sectionId == openSectionId,
                    onClick = { onOpenSection(section.sectionId) },
                )
            }
        }
    }
}

/** One section of a book. */
@Composable
private fun SectionRow(
    section: BookSection,
    selected: Boolean,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
) {
    Row(
        modifier = modifier
            .fillMaxWidth()
            .background(
                if (selected) MaterialTheme.colorScheme.primary.copy(alpha = 0.08f)
                else MaterialTheme.colorScheme.surface,
            )
            .clickable(onClick = onClick)
            .padding(start = 24.dp, end = 10.dp, top = 4.dp, bottom = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            section.title.ifBlank { "Section ${section.order + 1}" },
            style = MaterialTheme.typography.bodySmall,
            color = if (selected) MaterialTheme.colorScheme.primary
            else MaterialTheme.colorScheme.onSurface,
            modifier = Modifier.weight(1f),
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
        )
        Text(
            "${section.wordCount} w",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

/**
 * The section on screen.
 *
 * It is the studio's paper: a bounded sheet on the warm surface, the
 * manuscript's serif body type, the same ruler. The only difference is that
 * the body is editable -- the text is the operator's, and only Save writes it
 * to the server.
 */
@Composable
private fun SectionEditor(
    workspace: DocumentWorkspace,
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
                        workspace.openBook == null -> {
                            Text(
                                "No book is open.",
                                style = MaterialTheme.typography.titleLarge,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                            Spacer(Modifier.height(12.dp))
                            Text(
                                "Open a .docx, or pick a book from the library, to start editing.",
                                style = MaterialTheme.typography.bodyLarge,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }

                        workspace.openSectionId == null -> {
                            Text(
                                "No section is open.",
                                style = MaterialTheme.typography.titleLarge,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                            Spacer(Modifier.height(12.dp))
                            Text(
                                "Choose a section from the library to read and edit it.",
                                style = MaterialTheme.typography.bodyLarge,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }

                        else -> {
                            Text(
                                "SECTION",
                                style = MaterialTheme.typography.labelSmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                                letterSpacing = 2.sp,
                            )
                            Spacer(Modifier.height(10.dp))
                            Text(
                                workspace.sectionTitle.ifBlank { "Untitled section" },
                                style = MaterialTheme.typography.titleLarge,
                            )
                            Spacer(Modifier.height(22.dp))
                            OutlinedTextField(
                                value = workspace.sectionDraft,
                                onValueChange = controller::setSectionDraft,
                                textStyle = MaterialTheme.typography.bodyLarge.copy(
                                    color = MaterialTheme.colorScheme.onSurface,
                                ),
                                placeholder = {
                                    Text(
                                        "This section is empty. Write it here.",
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
                    "Section text · Save writes this section back to the server",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
    }
}

/**
 * The strip along the bottom.
 *
 * It carries the same things the studio's own strip carries: what the
 * workspace is doing, and what went wrong. A refused request must never look
 * like an empty shelf.
 */
@Composable
private fun DocumentsStatusStrip(
    state: StudioState,
    controller: StudioController,
    modifier: Modifier = Modifier,
) {
    val workspace = state.documents
    val book = workspace.openBook
    val sections = book?.sectionCount ?: 0

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
                if (workspace.saving) "Saving" else "Documents",
                style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.primary,
                maxLines = 1,
            )
            Text(
                "${workspace.books.size} book(s) · $sections section(s)",
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

/**
 * The name the save dialog offers for the book.
 *
 * It is the book's title as a file name: the characters a file name cannot
 * hold are replaced, and the `.docx` suffix is added exactly once.
 */
private fun suggestedFileName(title: String): String {
    val cleaned = title.replace(Regex("[\\\\/:*?\"<>|]"), "_").trim().ifBlank { "document" }
    return if (cleaned.endsWith(".docx", ignoreCase = true)) cleaned else "$cleaned.docx"
}
