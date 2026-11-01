# Author a real Word document that carries a chart.
#
# The synthetic package the parts tests build is not a document Word can open:
# its chart part has no series and its embedded object is not a compound file.
# Only a document Word itself wrote can say whether a carried chart is still a
# chart after a round trip.
#
# The result is committed as tests/fixtures/word_chart_fixture.docx so that the
# conformance check does not depend on Excel being installed.  Regenerate it with:
#
#     powershell -NoProfile -ExecutionPolicy Bypass -File tests\word_make_chart.ps1 `
#         -Path tests\fixtures\word_chart_fixture.docx

param(
    [Parameter(Mandatory = $true)][string]$Path
)

$ErrorActionPreference = 'Stop'

$wdAlertsNone = 0
$wdDoNotSaveChanges = 0
$wdFormatXMLDocument = 12
# 51 = xlColumnClustered, the chart Word inserts by default.
$xlColumnClustered = 51

$word = New-Object -ComObject Word.Application
$word.Visible = $false
$word.DisplayAlerts = $wdAlertsNone
$doc = $null

try {
    $doc = $word.Documents.Add()
    $doc.Content.Text = "A document with a chart in it."

    $added = $false
    try {
        $null = $doc.InlineShapes.AddChart2(-1, $xlColumnClustered)
        $added = $true
        Write-Output "CHART=inline"
    }
    catch {
        Write-Output "CHART=inline failed: $($_.Exception.Message)"
    }
    if (-not $added) {
        try {
            $null = $doc.Shapes.AddChart2(-1, $xlColumnClustered)
            $added = $true
            Write-Output "CHART=floating"
        }
        catch {
            Write-Output "CHART=floating failed: $($_.Exception.Message)"
        }
    }
    if (-not $added) {
        try {
            $null = $doc.InlineShapes.AddChart()
            $added = $true
            Write-Output "CHART=legacy"
        }
        catch {
            Write-Output "CHART=legacy failed: $($_.Exception.Message)"
        }
    }
    if (-not $added) {
        Write-Output "SAVED=no"
        Write-Output "ERROR=no chart could be inserted; Excel is probably not installed"
        exit 1
    }

    $resolved = [System.IO.Path]::GetFullPath($Path)
    $doc.SaveAs2($resolved, $wdFormatXMLDocument)
    Write-Output "SAVED=yes"
    Write-Output "INLINE_SHAPES=$($doc.InlineShapes.Count)"
}
catch {
    Write-Output "SAVED=no"
    Write-Output "ERROR=$($_.Exception.Message)"
    exit 1
}
finally {
    if ($null -ne $doc) { $doc.Close($wdDoNotSaveChanges) }
    $word.Quit()
    [System.Runtime.InteropServices.Marshal]::ReleaseComObject($word) | Out-Null
}
