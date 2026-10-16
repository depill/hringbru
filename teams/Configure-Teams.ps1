# Run after Connect-MicrosoftTeams -TenantId <your tenant GUID>.
# This creates only objects under ResourcePrefix and appends one PSTN usage.
# Use -WhatIf to preview. No user assignments are made by this script.
[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory)][ValidatePattern('^[a-z0-9.-]+\.[a-z]{2,63}$')][string]$SbcFqdn,
    [Parameter(Mandatory)][guid]$ExpectedTenantId,
    [ValidateSet('Hringbru', 'Sipsip')][string]$ResourcePrefix = 'Hringbru',
    [ValidateRange(1,1000)][int]$MaxConcurrentSessions = 20,
    [switch]$AllowInternational
)
$ErrorActionPreference = 'Stop'
$tenant = Get-CsTenant
if ([guid]$tenant.TenantId -ne $ExpectedTenantId) { throw 'Connected Teams tenant does not match ExpectedTenantId.' }
if ($SbcFqdn.EndsWith('.onmicrosoft.com')) { throw 'Use a verified custom domain.' }
$gateway = Get-CsOnlinePSTNGateway | Where-Object Identity -eq $SbcFqdn
$gatewayArgs = @{
    SipSignalingPort = 5061; Enabled = $true; SendSipOptions = $true
    MediaBypass = $false; ForwardPai = $true; MaxConcurrentSessions = $MaxConcurrentSessions
}
if ($PSCmdlet.ShouldProcess($SbcFqdn, 'Configure SIP/TLS gateway (no media bypass)')) {
    if ($gateway) { Set-CsOnlinePSTNGateway -Identity $SbcFqdn @gatewayArgs }
    else { New-CsOnlinePSTNGateway -Identity $SbcFqdn @gatewayArgs }
}
$usage = "$ResourcePrefix-Hringdu"
$dialPlanName = "$ResourcePrefix-Iceland"
if ($usage -notin (Get-CsOnlinePstnUsage -Identity Global).Usage) {
    if ($PSCmdlet.ShouldProcess('Global PSTN usages', "Append $usage")) {
        Set-CsOnlinePstnUsage -Identity Global -Usage @{Add=$usage}
    }
}
$pattern = '^\+354\d{7}$|^112$'
if ($AllowInternational) { $pattern = '^\+[1-9]\d{6,14}$|^112$' }
$route = Get-CsOnlineVoiceRoute | Where-Object Identity -eq $usage
if ($PSCmdlet.ShouldProcess($usage, 'Configure outbound voice route')) {
    $routeArgs = @{NumberPattern=$pattern; OnlinePstnGatewayList=@($SbcFqdn); OnlinePstnUsages=@($usage); Priority=1}
    if ($route) { Set-CsOnlineVoiceRoute -Identity $usage @routeArgs }
    else { New-CsOnlineVoiceRoute -Identity $usage @routeArgs }
}
$policy = Get-CsOnlineVoiceRoutingPolicy | Where-Object Identity -eq "Tag:$usage"
if ($PSCmdlet.ShouldProcess($usage, 'Configure voice routing policy')) {
    if ($policy) { Set-CsOnlineVoiceRoutingPolicy -Identity $usage -OnlinePstnUsages @($usage) }
    else { New-CsOnlineVoiceRoutingPolicy -Identity $usage -OnlinePstnUsages @($usage) }
}
# National dialling and 00 international prefix. 112 remains unchanged.
$rules = @(
    New-CsVoiceNormalizationRule -Parent $dialPlanName -Name 'Iceland-seven-digits' -Pattern '^(\d{7})$' -Translation '+354$1' -InMemory
    New-CsVoiceNormalizationRule -Parent $dialPlanName -Name 'International-00' -Pattern '^00([1-9]\d{6,14})$' -Translation '+$1' -InMemory
    New-CsVoiceNormalizationRule -Parent $dialPlanName -Name 'Emergency-112' -Pattern '^(112)$' -Translation '$1' -InMemory
)
$dialPlan = Get-CsTenantDialPlan | Where-Object Identity -eq "Tag:$dialPlanName"
if ($PSCmdlet.ShouldProcess($dialPlanName, 'Configure tenant dial plan')) {
    if ($dialPlan) { Set-CsTenantDialPlan -Identity $dialPlanName -NormalizationRules $rules }
    else { New-CsTenantDialPlan -Identity $dialPlanName -NormalizationRules $rules }
}
Write-Host 'Prepared gateway, route, routing policy and dial plan. Check SBC health before assigning users.'
