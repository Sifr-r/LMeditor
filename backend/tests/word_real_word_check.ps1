# Real-Word conformance check for a .docx.
#
# Opens the package in Microsoft Word itself (COM, invisible, read-only) and
# reports the structure Word actually sees.  Word is the reference
# implementation, so a property it reports is evidence that no amount of
# package-level assertion can replace: Word has behaviours that a well-formed
# package can still violate (see W11 in WORD_PROCESSING_CONTRACT.md).
#
# The output is `KEY=VALUE` lines, which the Python driver parses.
#
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File check_in_word.ps1 -Path <file.docx>
#
# Requires Word to be installed.  The caller is responsible for skipping.

param(
    [Parameter(Mandatory = $true)][string]$Path
)

$ErrorActionPreference = 'Stop'

$wdAlertsNone = 0
$wdDoNotSaveChanges = 0
$wdStatisticWords = 0
$wdStatisticPages = 2
$wdActiveEndPageNumber = 3

function Get-Prop($collection, $name) {
    # BuiltInDocumentProperties is a parameterized COM property; PowerShell
    # cannot call it directly, so late-bind through InvokeMember.
    try {
        $prop = [System.__ComObject].InvokeMember(
            "Item", [System.Reflection.BindingFlags]::GetProperty,
            $null, $collection, @($name))
        return [System.__ComObject].InvokeMember(
            "Value", [System.Reflection.BindingFlags]::GetProperty,
            $null, $prop, $null)
    } catch {
        return $null
    }
}

$resolved = (Resolve-Path -LiteralPath $Path).Path
$word = New-Object -ComObject Word.Application
$word.Visible = $false
$word.DisplayAlerts = $wdAlertsNone
$doc = $null

try {
    # ConfirmConversions = false, ReadOnly = true, AddToRecentFiles = false.
    $doc = $word.Documents.Open($resolved, $false, $true, $false)
    $doc.Repaginate()

    Write-Output "OPENED=yes"
    Write-Output "WORDS=$($doc.ComputeStatistics($wdStatisticWords))"
    Write-Output "PAGES=$($doc.ComputeStatistics($wdStatisticPages))"
    Write-Output "PARAGRAPHS=$($doc.Paragraphs.Count)"
    Write-Output "PROP_TITLE=$(Get-Prop $doc.BuiltInDocumentProperties 'Title')"
    Write-Output "PROP_AUTHOR=$(Get-Prop $doc.BuiltInDocumentProperties 'Author')"
    Write-Output "PROP_SUBJECT=$(Get-Prop $doc.BuiltInDocumentProperties 'Subject')"
    Write-Output "PROP_KEYWORDS=$(Get-Prop $doc.BuiltInDocumentProperties 'Keywords')"

    Write-Output "--- paragraphs ---"
    for ($i = 1; $i -le $doc.Paragraphs.Count; $i++) {
        $p = $doc.Paragraphs.Item($i)
        $text = $p.Range.Text -replace "[`r`a`v]", ''
        $style = $p.Range.Style.NameLocal
        $listType = $p.Range.ListFormat.ListType
        $listLevel = $p.Range.ListFormat.ListLevelNumber
        # ListString is the marker Word renders for the item: "i." for a roman
        # level, "a)" for an alpha one.  It is the only way to see whether the
        # numbering format of the source document survived.
        $listString = $p.Range.ListFormat.ListString
        $page = $p.Range.Information($wdActiveEndPageNumber)
        Write-Output ("P{0}|style={1}|list={2}|level={3}|marker={4}|page={5}|text={6}" -f $i, $style, $listType, $listLevel, $listString, $page, $text)
    }

    Write-Output "--- tables ---"
    Write-Output "TABLE_COUNT=$($doc.Tables.Count)"
    for ($t = 1; $t -le $doc.Tables.Count; $t++) {
        $table = $doc.Tables.Item($t)
        $cols = "?"
        try { $cols = $table.Columns.Count } catch { $cols = "ERR" }
        Write-Output "TABLE${t}|rows=$($table.Rows.Count)|cols=$cols"
        for ($r = 1; $r -le $table.Rows.Count; $r++) {
            $cells = @()
            try {
                for ($c = 1; $c -le $table.Rows.Item($r).Cells.Count; $c++) {
                    $cell = $table.Rows.Item($r).Cells.Item($c)
                    $ct = $cell.Range.Text -replace "[`r`a`v]", ''
                    $cells += ("'{0}' (w={1:N1}pt)" -f $ct, $cell.Width)
                }
            } catch {
                $cells += "ERR:$($_.Exception.Message)"
            }
            Write-Output ("TABLE${t}ROW${r}|" + ($cells -join ' || '))
        }
        # Word refuses Cell(row, col) addressing when a vertical merge is
        # present, which is how a vertical merge is detected at all.
        try {
            $null = $table.Cell(2, 1)
            Write-Output "TABLE${t}|vertical_merge=no"
        } catch {
            Write-Output "TABLE${t}|vertical_merge=yes"
        }
    }

    Write-Output "--- footnotes ---"
    Write-Output "FOOTNOTE_COUNT=$($doc.Footnotes.Count)"
    for ($f = 1; $f -le $doc.Footnotes.Count; $f++) {
        $ft = $doc.Footnotes.Item($f).Range.Text -replace "[`r`a`v]", ''
        Write-Output "FOOTNOTE${f}=$ft"
    }

    Write-Output "--- hyperlinks ---"
    Write-Output "HYPERLINK_COUNT=$($doc.Hyperlinks.Count)"
    for ($h = 1; $h -le $doc.Hyperlinks.Count; $h++) {
        $hl = $doc.Hyperlinks.Item($h)
        Write-Output "HYPERLINK${h}|address=$($hl.Address)|display=$($hl.TextToDisplay)"
    }

    Write-Output "--- comments ---"
    Write-Output "COMMENT_COUNT=$($doc.Comments.Count)"
    for ($c = 1; $c -le $doc.Comments.Count; $c++) {
        $cm = $doc.Comments.Item($c)
        $ct = $cm.Range.Text -replace "[`r`a`v]", ''
        Write-Output "COMMENT${c}|author=$($cm.Author)|initials=$($cm.Initial)|text=$ct"
    }

    Write-Output "--- endnotes ---"
    Write-Output "ENDNOTE_COUNT=$($doc.Endnotes.Count)"
    for ($e = 1; $e -le $doc.Endnotes.Count; $e++) {
        $et = $doc.Endnotes.Item($e).Range.Text -replace "[`r`a`v]", ''
        Write-Output "ENDNOTE${e}=$et"
    }

    Write-Output "--- revisions ---"
    Write-Output "REVISION_COUNT=$($doc.Revisions.Count)"
    for ($v = 1; $v -le $doc.Revisions.Count; $v++) {
        $rev = $doc.Revisions.Item($v)
        $vt = $rev.Range.Text -replace "[`r`a`v]", ''
        Write-Output "REVISION${v}|type=$($rev.Type)|author=$($rev.Author)|text=$vt"
    }

    Write-Output "--- sections ---"
    Write-Output "SECTION_COUNT=$($doc.Sections.Count)"
    for ($s = 1; $s -le $doc.Sections.Count; $s++) {
        $sec = $doc.Sections.Item($s)
        $setup = $sec.PageSetup
        Write-Output ("SECTION{0}|orientation={1}|page_width={2:N1}|page_height={3:N1}|top_margin={4:N1}|left_margin={5:N1}" -f `
            $s, $setup.Orientation, $setup.PageWidth, $setup.PageHeight, $setup.TopMargin, $setup.LeftMargin)
        # 1 = primary, 2 = first page, 3 = even pages.
        Write-Output "SECTION${s}|header_default=$(($sec.Headers.Item(1).Range.Text) -replace "[`r`a`v]", '')"
        Write-Output "SECTION${s}|header_first=$(($sec.Headers.Item(2).Range.Text) -replace "[`r`a`v]", '')"
        Write-Output "SECTION${s}|header_even=$(($sec.Headers.Item(3).Range.Text) -replace "[`r`a`v]", '')"
        Write-Output "SECTION${s}|footer_default=$(($sec.Footers.Item(1).Range.Text) -replace "[`r`a`v]", '')"
    }

    Write-Output "--- inline shapes ---"
    Write-Output "INLINE_SHAPE_COUNT=$($doc.InlineShapes.Count)"
    for ($sh = 1; $sh -le $doc.InlineShapes.Count; $sh++) {
        $shape = $doc.InlineShapes.Item($sh)
        Write-Output "INLINE_SHAPE${sh}|type=$($shape.Type)|width=$($shape.Width)|height=$($shape.Height)"
    }
}
catch {
    Write-Output "OPENED=no"
    Write-Output "ERROR=$($_.Exception.Message)"
}
finally {
    if ($null -ne $doc) { $doc.Close($wdDoNotSaveChanges) }
    $word.Quit()
    [System.Runtime.InteropServices.Marshal]::ReleaseComObject($word) | Out-Null
}
