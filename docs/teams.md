# Configure Microsoft Teams

Run these commands from the repository root on your administrator workstation. Replace the example hostname and tenant GUID with your own values.

Use a current MicrosoftTeams PowerShell module with an authorized Teams administrator. Connect explicitly to the intended tenant. These scripts have not been run against a live tenant.

```powershell
Install-Module MicrosoftTeams -Scope CurrentUser
Connect-MicrosoftTeams -TenantId '<tenant-guid>'
./teams/Configure-Teams.ps1 -SbcFqdn 'sbc.example.com' -ExpectedTenantId '<tenant-guid>' -WhatIf
./teams/Configure-Teams.ps1 -SbcFqdn 'sbc.example.com' -ExpectedTenantId '<tenant-guid>'
Get-CsOnlinePSTNGateway -Identity 'sbc.example.com'
```

The script creates/updates Hringbru-named routing objects, appends one global PSTN usage, enables OPTIONS and keeps media bypass off. It does not assign users. Existing unrelated tenant objects are preserved; rerunning does update the named Hringbru objects. A preexisting gateway with the supplied FQDN is updated. National 7-digit dialling normalizes to `+354`; international `00` normalizes to `+`; `112` is preserved.

For a tenant already configured under the former project name, add `-ResourcePrefix Sipsip` to both `Configure-Teams.ps1` and `Assign-Users.ps1`. This retains the existing `Sipsip-Hringdu` and `Sipsip-Iceland` objects and assignments. New tenants use the default `Hringbru` prefix.

Generate your blank mapping with `python3 scripts/render.py --users-template teams/users.csv`. The script reads `DID_NUMBERS` from the private `.env` (override `-EnvPath` if needed). Fill only explicitly agreed user mappings in `teams/users.csv`, then:

```powershell
./teams/Assign-Users.ps1 -ExpectedTenantId '<tenant-guid>' -WhatIf
./teams/Assign-Users.ps1 -ExpectedTenantId '<tenant-guid>'
```

Blank rows are skipped. The script validates the complete mapping, rejects duplicate users/numbers, foreign number ranges, synchronized on-premises phone attributes and conflicting existing assignments. Assignments and policy changes are sequential; a remote failure can leave a partial application, so review readback before rerunning. Verify licensing, Teams Only mode and resource-account design separately. Numbers route inbound as E.164 DIDs and therefore resolve using Teams' own number assignments.
