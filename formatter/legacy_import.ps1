param([string]$Source, [string]$Target)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$app = $null
$doc = $null
$owned = $false
try {
    Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class WindowProcessId {
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint processId);
}
'@
    $before = @(Get-Process | ForEach-Object { $_.Id })
    foreach ($id in @('Word.Application', 'KWPS.Application')) {
        try { $app = New-Object -ComObject $id; break } catch { $app = $null }
    }
    if ($null -eq $app) { throw 'Neither Word.Application nor KWPS.Application COM is available' }
    $pidValue = [uint32]0
    [void][WindowProcessId]::GetWindowThreadProcessId([IntPtr][int64]$app.Hwnd, [ref]$pidValue)
    if ($pidValue -eq 0 -or $before -contains [int]$pidValue) {
        throw 'Cannot prove the automation process is newly created; refusing to use or close it'
    }
    $owned = $true
    $app.Visible = $false
    $app.DisplayAlerts = 0
    $app.AutomationSecurity = 3
    if ($app.AutomationSecurity -ne 3) { throw 'Cannot disable document macros' }
    $app.Options.UpdateLinksAtOpen = $false
    if ($app.Options.UpdateLinksAtOpen) { throw 'Cannot disable external link updates' }
    $doc = $app.Documents.Open($Source, $false, $true, $false, '', '', $true, '', '', 0, '', $false, $false, 0, $true, $false)
    if (-not $doc.ReadOnly) { throw 'Source was not opened read-only' }
    foreach ($name in @('Tables', 'InlineShapes', 'Shapes', 'Fields', 'Hyperlinks', 'Revisions', 'Comments', 'Bookmarks', 'Footnotes', 'Endnotes', 'ContentControls')) {
        if ($doc.$name.Count -gt 0) { throw "Unsupported legacy document structure: $name" }
    }
    if ($doc.Sections.Count -ne 1) { throw 'Multiple sections unsupported' }
    foreach ($section in $doc.Sections) {
        foreach ($header in $section.Headers) { if ($header.Range.Text.Trim("`r", "`n", ' ') -or $header.Shapes.Count) { throw 'Headers unsupported' } }
        foreach ($footer in $section.Footers) { if ($footer.Range.Text.Trim("`r", "`n", ' ') -or $footer.Shapes.Count) { throw 'Footers unsupported' } }
    }
    $paragraphs = @()
    foreach ($paragraph in $doc.Paragraphs) {
        if ($paragraph.Range.ListFormat.ListType -ne 0) { throw 'Automatic numbering unsupported' }
        $text = $paragraph.Range.Text
        if (-not $text.EndsWith("`r")) { throw 'Unexpected paragraph termination' }
        $text = $text.Substring(0, $text.Length - 1)
        if ([string]::IsNullOrWhiteSpace($text) -or $text -match '[\x00-\x1f]') { throw 'Empty paragraph or unsupported control character' }
        $paragraphs += $text
    }
    if ($paragraphs.Count -eq 0) { throw 'Empty document' }
    $doc.SaveAs2($Target, 16)
    $doc.Close(0)
    $doc = $null
    @{ok=$true; paragraphs=@($paragraphs)} | ConvertTo-Json -Compress -Depth 5
} catch {
    @{ok=$false; error=$_.Exception.Message} | ConvertTo-Json -Compress
    exit 2
} finally {
    if ($null -ne $doc) { try { $doc.Close(0) } catch {} }
    if ($owned -and $null -ne $app) { try { $app.Quit() } catch {} }
    if ($null -ne $app) { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($app) }
}
