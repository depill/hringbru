$ErrorActionPreference = 'Stop'
foreach ($path in Get-ChildItem "$PSScriptRoot/../teams/*.ps1") {
    $tokens = $null; $parseErrors = $null
    [System.Management.Automation.Language.Parser]::ParseFile($path.FullName, [ref]$tokens, [ref]$parseErrors) | Out-Null
    if ($parseErrors.Count) { throw ($parseErrors | Out-String) }
    Write-Host "PASS: syntax $($path.Name)"
}
