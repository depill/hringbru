# Blank template rows are skipped. Supports regular licensed users only.
# Call queues / auto attendants require separately configured resource accounts.
[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory)][guid]$ExpectedTenantId,
    [ValidateSet('Hringbru', 'Sipsip')][string]$ResourcePrefix = 'Hringbru',
    [string]$EnvPath = "$PSScriptRoot/../.env",
    [string]$CsvPath = "$PSScriptRoot/users.csv"
)
$ErrorActionPreference = 'Stop'
$tenant = Get-CsTenant
if ([guid]$tenant.TenantId -ne $ExpectedTenantId) { throw 'Connected Teams tenant does not match ExpectedTenantId.' }
# Read only the allowlist from the literal settings; never evaluate shell expressions.
$didLines = @(Get-Content -LiteralPath $EnvPath | Where-Object { $_ -match '^DID_NUMBERS=' })
if ($didLines.Count -ne 1) { throw 'Set exactly one DID_NUMBERS entry in your private .env.' }
$allowed = @($didLines[0].Substring(12).Split(',') | ForEach-Object { $_.Trim() })
if ($allowed.Count -gt 200 -or @($allowed | Where-Object { $_ -notmatch '^\+354[0-9]{7}$' }).Count -gt 0) {
    throw 'DID_NUMBERS must contain explicit Icelandic E.164 numbers.'
}
$rows = @(Import-Csv -LiteralPath $CsvPath)
$numbers = @{}; $users = @{}; $plan = @()
foreach ($row in $rows) {
    if ($row.PhoneNumber -notin $allowed) { throw "Number outside DID_NUMBERS: $($row.PhoneNumber)" }
    if ($numbers.ContainsKey($row.PhoneNumber)) { throw "Duplicate number: $($row.PhoneNumber)" }
    $numbers[$row.PhoneNumber] = $true
    if ([string]::IsNullOrWhiteSpace($row.UserPrincipalName)) { continue }
    if ($row.UserPrincipalName -notmatch '^[^\s@]+@[^\s@]+$') { throw 'Invalid UserPrincipalName' }
    if ($users.ContainsKey($row.UserPrincipalName)) { throw "Duplicate user: $($row.UserPrincipalName)" }
    $users[$row.UserPrincipalName] = $true
    $user = Get-CsOnlineUser -Identity $row.UserPrincipalName
    if ($user.LineUri -and $user.LineUri -ne "tel:$($row.PhoneNumber)") {
        throw "Existing different phone number on $($row.UserPrincipalName); review before changing."
    }
    if ($user.OnPremLineUri) { throw "On-premises phone attributes on $($row.UserPrincipalName); review synchronization first." }
    $assigned = @(Get-CsPhoneNumberAssignment -TelephoneNumber $row.PhoneNumber)
    foreach ($a in $assigned) {
        if ($a.AssignedPstnTargetId -and [string]$a.AssignedPstnTargetId -ne [string]$user.Identity) {
            throw "Number already belongs to another identity: $($row.PhoneNumber)"
        }
    }
    $plan += $row
}
# Validate the complete mapping before the first write.
$plan | Format-Table PhoneNumber,UserPrincipalName
foreach ($row in $plan) {
    if ($PSCmdlet.ShouldProcess($row.UserPrincipalName, "Assign $($row.PhoneNumber), $ResourcePrefix voice policy and Iceland dial plan")) {
        Set-CsPhoneNumberAssignment -Identity $row.UserPrincipalName -PhoneNumber $row.PhoneNumber -PhoneNumberType DirectRouting
        Grant-CsOnlineVoiceRoutingPolicy -Identity $row.UserPrincipalName -PolicyName "$ResourcePrefix-Hringdu"
        Grant-CsTenantDialPlan -Identity $row.UserPrincipalName -PolicyName "$ResourcePrefix-Iceland"
        Get-CsOnlineUser -Identity $row.UserPrincipalName | Select-Object UserPrincipalName,LineUri,EnterpriseVoiceEnabled,OnlineVoiceRoutingPolicy,TenantDialPlan
    }
}
