# Acceptance record

## Local checks

Verified 2026-10-16 on macOS with Docker Linux/arm64:

- `docker compose build asterisk`: passed, Asterisk 22.11.0 from SHA-256-verified upstream source.
- `docker compose config --quiet`: passed.
- `python3 -m unittest discover -s tests -v`: 4 tests passed (configuration injection, source restrictions, credential escaping and TLS configuration).
- `python3 tests/smoke.py`: passed with network disabled; 4 SIP endpoints, TLS/SRTP modules, all 7 currently documented Microsoft CA roots, 10 number-normalization cases and 6 rejected call routes. Captured a verified-TLS INVITE with hostname Contact/Via and SDES-SRTP, plus a national-format carrier INVITE with the validated caller DID in PAI. Tested password parsing with a leading `>`, a backslash and a semicolon.
- `tests/Check-PowerShell.ps1` in Microsoft's PowerShell 7.5 Linux container: both Teams scripts parsed successfully; no tenant commands executed.
- Generated firewall checked with `nft -c` inside an isolated Debian 13 container: passed. Host firewall was not modified.
- Temporary test containers were removed. No production container is running.

The image was built for arm64 only. Repeat the build and smoke test on the destination server architecture. Asterisk 22.11.0 emits a known upstream XML documentation warning for `stasis/threadpool/minimum_size`; configuration and runtime checks pass despite this warning.

Public IP, DNS activation, certificates and tenant/carrier connectivity remain unverified. Deployment-specific hostnames and account settings belong in the private configuration.

## Ansible deployment follow-up

Verified locally on 2026-10-16:

- `./bin/deploy --syntax-check`: passed with Ansible Core 2.20.5; no SSH connection.
- Example inventory resolves to exactly one bridge host. Actual inventory remains unconfigured.
- `python3 -m unittest discover -s tests -v`: 12 tests passed, including configuration checks and simulated first deployment, unchanged redeployment, active-call rejection, DNS mismatch, failed health checks, and empty/multiple inventory rejection.
- Deployment payload uses a source allowlist; private settings/password transfer suppresses logging and diffs. Render previews, inventory, local secrets and runtime data are excluded from Docker builds.
- No Ansible provisioning, systemd changes, certificate issuance or firewall deployment has been executed against a server. Server-side integration and reboot behavior remain unverified.

## Minimal Debian host audit

Read-only SSH checks on 2026-10-16 confirmed Debian 13, x86_64, systemd, four CPUs, about 4 GB RAM and 27 GB available disk. Python 3.13, Python apt bindings, CA certificates, OpenSSL, nftables, util-linux and iproute2 are installed. Time synchronization and verified HTTPS access to Debian, Docker and Let's Encrypt passed. The configured local subnet and public DNS match the intended server configuration.

Sudo and the Docker Engine/Buildx/Compose stack are absent, and the SSH account needs sudo-group membership. No privileged provisioning has been performed on that server. The reusable audit returned `changed=0`.

The bootstrap package/group script was exercised twice in a disposable Debian 13 container: the first run installed missing dependencies and granted the test account sudo membership; the second reported no changes. All three Ansible entry points pass syntax checks and 15 configuration/deployment tests pass. Bootstrap via the actual server's root password, Docker provisioning, reboot behavior and live calling remain pending.

## First server deployment and permission repair

Follow-up evidence on 2026-10-16 supersedes the initial provisioning status above:

- Read-only SSH confirmed sudo-group membership and the required sudo, Python, Docker Engine, Buildx and Compose packages are now installed.
- The operator-provided Ansible output confirms the amd64 image built successfully and Let's Encrypt issued and installed a hostname-matching certificate. Asterisk failed its startup health check; the first release was not promoted.
- Regression tests reproduced a TLS directory created as root-only under umask 0077, plus five generated configuration files inaccessible to container UID 10001. Certificate installation now repairs directory and file modes, including unchanged certificates, and rendering sets readable modes on all mounted configuration files. The shared parent remains private.
- All 17 unit tests and Ansible deployment syntax checks pass. The offline arm64 container smoke test also passes using the production certificate installer and restrictive configuration/installation umask.
- Applying the repair on the server, live registration, Teams pairing, renewal and reboot validation remain pending. No successful server health check is claimed.

## Deployment inputs to confirm privately

- Server OS/architecture, SSH target, public IPv4, SBC FQDN and NAT subnets.
- Verified Teams domain and tenant GUID; Teams Phone licensing and user/queue mapping.
- Hringdu password; account entitlement for all 20 DIDs and concurrent channels.
- Hringdu signalling/media source networks, transport, DID delivery and caller-ID rules.

## Live signalling evidence

The operator-provided server output on 2026-10-16 confirms Hringdu registration and Microsoft OPTIONS responses of `200 OK`. A Microsoft OPTIONS request also reached Asterisk and received `200 OK`, proving SIP signalling in both directions. An outbound Teams call reached the carrier and received `180 Ringing` and `183 Session Progress`; the carrier's early RTP reached Asterisk. The Teams side then sent CANCEL before answer, followed by the expected `487` termination response.

This trace does not prove an answered call, two-way speech, presented caller ID, incoming PSTN calls or Teams dashboard health. Raw traces are not stored in this public record because they contain account identifiers, personal information, digest material and SRTP keys.

## Hringbrú rename

Verified locally on 2026-10-16: the renamed `hringbru-asterisk:22.11.0` arm64 image builds and passes the offline smoke test; all 17 unit tests, three Ansible entry-point syntax checks, deployment input validation and both Teams PowerShell syntax checks pass. Compose resolution confirms that a new installation uses `hringbru` and its own named data volume, while the private compatibility settings preserve the existing `sipsip` project and volume. Existing server paths, service names and maintenance locks can also be retained, and both Teams scripts accept the previous resource prefix. The renamed code has not been deployed to the server.

## Live validation status

- [ ] DNS A record / tenant verified-domain activation / firewall rules.
- [ ] Issued certificate, full chain, hostname, trusted Teams CAs and mutual TLS.
- [x] Hringdu Registered; Microsoft OPTIONS exchanged with `200 OK` in both directions.
- [ ] Teams gateway dashboard healthy.
- [ ] Inbound 4129900–4129919 mapped to agreed users/resources.
- [ ] Outbound mobile/landline and verified caller ID from each assigned DID.
- [ ] Bidirectional audio, DTMF, hold/resume, hangup and >30-minute call.
- [ ] Busy/no-answer, anonymity, transfer/forwarding, voicemail and queues as applicable.
- [ ] Emergency service/location arrangements validated with carrier.
- [ ] Untrusted source / out-of-range DID and CLI rejected.
- [ ] Peer failure and recovery; certificate renewal and fresh TLS sessions.
- [ ] Monitoring, firewall persistence, backups and restart after host reboot.

An image build or process health check does not complete these live acceptance items.
