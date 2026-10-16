# Operations

These instructions cover an Ansible-managed deployment. The [manual deployment reference](manual-deployment.md) uses a different directory layout; do not install its renewal units over an Ansible deployment.

## SSH account and sudo

Set `ansible_user` in the private inventory to an existing regular account with sudo access. The playbook explicitly uses `become: true`, `become_method: sudo` and `become_user: root` for server provisioning. Asterisk runs as UID/GID 10001 inside its container.

From your normal workstation account:

```sh
# Password-protected sudo: enter the remote account's sudo password when prompted.
./bin/deploy --ask-become-pass

# Passwordless sudo:
./bin/deploy
```

To verify SSH and sudo independently of the SIP settings, run this read-only check from the repository root:

```sh
ansible -i deploy/ansible/inventory.yml hringbru -b -K -m ansible.builtin.command -a 'id -u'
```

The result should be `0`, confirming that Ansible can elevate through sudo. Omit `-K` for passwordless sudo. Store credentials interactively or through an appropriate secret store, not as plaintext inventory variables. Normal deployment does not add Docker-group membership or change your sudo policy; the explicit bootstrap mode establishes sudo-group membership as described below.

The SSH examples below use `ssh -t` so an interactive sudo password prompt can work. Automatic certificate renewal uses the root systemd service and does not depend on an SSH session or interactive sudo prompt.

## Bootstrap a minimal server without sudo

Some minimal Debian installations have no `sudo` package or Python interpreter. Configure only the SSH inventory, then run a read-only audit:

```sh
./bin/deploy --check-server
```

The audit requires neither sudo nor Python. It reports installed prerequisites, OS/architecture/init system, capacity, interfaces, listening ports, NTP synchronization and the CA bundle. It does not validate inbound NAT forwarding, live SIP registration or Teams calls.

To install base prerequisites using an existing root password:

```sh
./bin/deploy --bootstrap
```

Ansible connects as the regular SSH account, then uses `su` to root. Enter the **server root password** at this bootstrap prompt. It installs missing packages only: sudo, Python 3 and apt bindings, CA certificates, OpenSSL, nftables, util-linux and iproute2. It adds the existing SSH account to the sudo group, validates sudoers, and closes the old connection so subsequent SSH sessions pick up the new membership. It does not change passwordless-sudo policy. Re-running an already complete bootstrap makes no package or group changes.

After bootstrap, deploy normally with `./bin/deploy --ask-become-pass`, entering the regular user's sudo password. Docker Engine, Buildx and Compose are installed by the normal deployment from Docker's signed apt repository. A host with working sudo but missing Python is bootstrapped before fact gathering.

If the server has no usable root password, use the following console/administrator alternative.

Use an existing root shell through the server console, another administrator, or `su -` with the root password. In that root shell:

```sh
apt-get update
apt-get install -y sudo python3
usermod -aG sudo YOUR_SSH_USER
```

Replace `YOUR_SSH_USER` with the regular account named in your private inventory. Disconnect and reconnect SSH so the new group membership takes effect. Then, as the regular user on the server, verify:

```sh
sudo -v
sudo id -u
```

The second command should print `0`. Back on the deployment workstation:

```sh
./bin/deploy --ask-become-pass
```

The prompt needs the regular account's sudo password. If you have neither a working administrator account nor a root password/console, an administrator must perform this bootstrap; the unprivileged SSH account cannot grant itself access. Root SSH login is not required for the normal deployment workflow.

## Files and releases

```text
/opt/hringbru/releases/<fingerprint>/   source, private settings and trunk password
/opt/hringbru/shared/                  rendered Asterisk config, certificates and ACME state
/opt/hringbru/current                  symlink to the last healthy release
```

Release and shared-data directories are root-only. Docker bind mounts expose the required child directories to the unprivileged Asterisk UID 10001. Do not loosen the private parent directory permissions.

`current` is promoted only after the container passes its health check. Named Docker volumes preserve Asterisk's writable data across container replacement. Images are pulled from the registry by commit tag or digest. Explicit `--build` opts into a server build. Old release directories are retained and contain private settings and passwords; archive or remove them according to your retention policy, preserving the active release and shared state.

## Routine changes

Edit the workstation's private `.env`, inventory or password file, then run:

```sh
./bin/deploy
```

An unchanged healthy release avoids a restart. Changes that require container replacement are refused when active channels are detected; retry after calls finish. A second check runs just before replacement. This is not seamless call migration: plan a quiet maintenance window for changed releases.

Before mounted configuration changes, deployment snapshots the previous Compose runtime with its exact Docker image ID, configuration, installed TLS files, firewall and success marker in `shared/rollback`. The previous image also receives a local `hringbru-rollback:<project>` tag, protecting it from dangling-image pruning. On startup failure, the helper stops the candidate, restores those files and recreates the prior container without pulling or building. `current` and the marker return to the prior release. The named data volume and ACME account/certificate archive are preserved.

The private `shared/deployment-pending.json` journal marks an unfinished transaction. Re-running `./bin/deploy` first attempts recovery. If active calls prevent recovery, the helper leaves the snapshot and journal intact and exits; retry after calls finish. An unresponsive Docker daemon, unreadable call count or failed restoration can also require intervention. Check for `ROLLED BACK` and healthy status; a retained old symlink alone is insufficient. Never delete a pending journal or its snapshot to force a deployment.

A first installation has no prior runtime. If it fails, the candidate is stopped, no success marker is created, and the restrictive application firewall remains applied. Inspect the failure before retrying. The snapshot does not provide database migrations, cross-host disaster recovery, or rollback of changes to host packages/systemd units.

If the first deployment fails, `current` does not exist yet. Inspect the container directly from your workstation:

```sh
ssh -t YOUR_SSH_TARGET 'sudo docker logs --tail=100 hringbru-asterisk-1'
```

Certificate installation and configuration rendering explicitly set permissions on mounted children for container UID 10001, even with the server's private umask. The parent shared directory remains root-only. Redeployment repairs the mounted TLS permissions using the existing valid certificate.

Previous manual `/opt/hringbru/.local` deployments require explicit migration of their private data to the shared layout. The playbook refuses to abandon that directory silently.

## CI images and registry access

The GitHub workflow runs on pushes to `main`, version tags, manual dispatch and pull requests. Every architecture runs unit tests, Ansible syntax checks, offline SIP/TLS/SRTP tests and the Docker rollback fixture. Only successful trusted builds publish architecture images; the final multi-platform commit tag appears after both architectures succeed. Pull requests never log in or push to the registry. Actions are pinned to commit SHAs; publishing uses the job's `GITHUB_TOKEN` with package-write permission.

Images are published under `ghcr.io/<repository-owner>/<repository-name>:sha-<full-commit>`, with an OCI source label linking them to the repository. Fork deployments must use `--image` to select their registry. No SIP settings, passwords, user mappings or certificates enter the image. CI uses only dummy fixtures and never deploys to the server or changes Teams.

**First publication:** Check the package visibility before anonymous deployment. This repository's initial publication is public and anonymous manifest access has been verified. If your fork's package is private, open your profile or organization **Packages → hringbru → Package settings → Change visibility → Public** to enable anonymous pulls. Alternatively, authenticate root's Docker client on the destination host using a token with `read:packages`; do not put that token in `.env`, inventory or this repository. Deployment fails before changing the running bridge if the image cannot be pulled. [GitHub container registry documentation](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry).

Use the manifest digest from the successful Actions job summary for a content-addressed deployment. Commit tags identify source revisions but can be rebuilt; a digest identifies exact image bytes. Do not remove images tagged `hringbru-rollback:*`, use `docker image prune -a`, or prune retained releases during a pending transaction. Both old releases and rollback snapshots contain private data.

For a deliberate source build:

```sh
./bin/deploy --build --ask-become-pass
```

## Existing Sipsip installations

Hringbrú was previously named Sipsip. New installations use Docker project `hringbru`, image `hringbru-asterisk`, server directory `/opt/hringbru`, and `hringbru-*` systemd services. Existing installations can retain their original container, named volume, storage path, maintenance lock and service names while running the renamed image and configuration.

To upgrade an existing Ansible installation, rename its inventory group from `sipsip` to `hringbru` and add these variables under that group, keeping the existing SSH settings:

```yaml
all:
  children:
    hringbru:
      vars:
        hringbru_install_root: /opt/sipsip
        hringbru_service_name: sipsip
      hosts:
        bridge:
          ansible_host: YOUR_SSH_TARGET
          ansible_user: YOUR_SSH_USER
```

Add this line to the private `.env`:

```dotenv
HRINGBRU_PROJECT_NAME=sipsip
```

Then use the usual `./bin/deploy --ask-become-pass`. Keeping the Docker project identity preserves the existing `sipsip_asterisk-data` volume and container name `sipsip-asterisk-1`; keeping the server path preserves certificates and ACME account state. For that installation, continue using `/opt/sipsip/current`, `sipsip-asterisk-1` and `sipsip-renew.*` in the operational commands below. Source filenames and image names use `hringbru` regardless of these compatibility settings. The updated firewall replaces the former `inet sipsip` table with `inet hringbru`.

The Teams scripts default to `Hringbru-Hringdu` and `Hringbru-Iceland`. If the tenant already uses the former `Sipsip-*` policies, pass `-ResourcePrefix Sipsip` to both Teams scripts to keep updating those same objects. Renaming the repository does not change the gateway FQDN or tenant assignments.

## Inspect the bridge

Replace `YOUR_SSH_TARGET` with the inventory's SSH address or alias:

```sh
ssh -t YOUR_SSH_TARGET 'sudo docker compose --project-directory /opt/hringbru/current ps'
ssh -t YOUR_SSH_TARGET 'sudo docker compose --project-directory /opt/hringbru/current logs --tail=100 asterisk'
ssh -t YOUR_SSH_TARGET 'sudo docker compose --project-directory /opt/hringbru/current exec -T asterisk asterisk -rx "pjsip show registrations"'
ssh -t YOUR_SSH_TARGET 'sudo docker compose --project-directory /opt/hringbru/current exec -T asterisk asterisk -rx "pjsip show contacts"'
ssh -t YOUR_SSH_TARGET 'sudo docker compose --project-directory /opt/hringbru/current exec -T asterisk asterisk -rx "pjsip show transports"'
```

Monitor container health, Hringdu registration, Teams SBC health, certificate expiry, renewal failures and representative real calls. Container health proves local process/module readiness; it does not prove the trunks work. Docker logs rotate by size. Asterisk 22.11.0 emits a known upstream XML documentation warning about `stasis/threadpool/minimum_size`; the smoke test permits that specific warning.

Teams AORs use `qualify_2xx_only=yes` so SIP errors cannot count as successful probes. Earlier releases used Asterisk's default, which can show `Avail` even for a `403` response. Confirm an actual `200 OK` to outgoing OPTIONS and an incoming Microsoft OPTIONS answered with `200 OK`; outbound contact status alone does not prove both directions work.

AMI, ARI, the HTTP interface and SIP phone registration are not enabled. Persistent CDR/CEL recording is disabled in the supplied configuration.

## Status checks and alerts

On your workstation, `./bin/deploy --status --ask-become-pass` reads the deployed version's checks through Ansible without deploying anything. On the server itself:

```sh
sudo python3 /opt/hringbru/current/scripts/status.py
sudo python3 /opt/hringbru/current/scripts/status.py --json
sudo systemctl list-timers hringbru-monitor.timer
sudo journalctl -u hringbru-monitor.service --since yesterday
```

For a legacy installation use `/opt/sipsip/current`, `sipsip-monitor.*` and add `--service-prefix sipsip` to direct status commands. Exit codes are 0 healthy, 1 warning and 2 critical. Planned deployment/renewal lock contention skips the sample.

Checks cover container health, Hringdu registration, all three Teams contact qualifications, installed certificate hostname/expiry, renewal timer/service result, application firewall table presence and disk space. One or two available Teams peers are a warning; none is critical. Certificate warnings begin at 14 days, critical at 3 days. These checks do not prove inbound Microsoft OPTIONS, two-way audio, user assignment, cloud firewall rules or end-to-end calls.

The minute timer sends local journal incidents after three bad samples, recovery after two good ones and six-hour reminders while unhealthy. State persists privately in `shared/monitor-state.json`. Maintenance samples are skipped. Failed webhook deliveries retry on later samples. There is no automatic restart or call placement. Monitor the host externally as well: a powered-off host cannot send alerts, and a failed timer cannot report its own failure.

For an optional webhook, write your HTTPS endpoint URL into the ignored `secrets/alert_webhook_url`, restrict it with `chmod 600 secrets/alert_webhook_url`, then deploy. Keep the destination blank/absent for journal-only monitoring. This is a generic JSON webhook, not a Teams/Slack-specific message format: point it at an endpoint or automation that accepts this payload:

```json
{"service":"hringbru","event":"incident","status":"critical","checked_at":"...","checks":{},"issues":[{"level":"critical","message":"Hringdu registration is not Registered."}]}
```

Events are `incident`, `reminder` and `recovery`. No raw SIP traces, phone numbers, passwords or private keys are included. URLs are withheld from error output, requests time out after ten seconds and redirects are refused. No external alert is sent unless a destination is configured; destination testing remains an operator step.

## Certificate renewal

Ansible installs and enables `hringbru-renew.timer`. It checks twice daily with a randomized delay. The renewal service and deployment use the same maintenance lock; if the lock is occupied, the competing operation fails instead of running concurrently.

```sh
ssh -t YOUR_SSH_TARGET 'sudo systemctl list-timers hringbru-renew.timer'
ssh -t YOUR_SSH_TARGET 'sudo journalctl -u hringbru-renew.service --since yesterday'
ssh -t YOUR_SSH_TARGET 'sudo python3 /opt/hringbru/current/scripts/certificates.py renew --dry-run'
```

Standalone ACME validation requires the hostname to resolve correctly and TCP 80 to remain reachable and unused. An AAAA record also requires working validation over IPv6. Adapt issuance to webroot or DNS validation before sharing the server with an existing web server.

Changed certificates are installed with their full chain after hostname, lifetime and key-match checks. The service requests a graceful Asterisk restart, which stops accepting new calls while active calls drain and then establishes fresh TLS connections. This creates a brief service gap on a single host.

For a manual certificate operation that installs or renews a certificate, acquire the shared lock too:

```sh
ssh -t YOUR_SSH_TARGET 'sudo flock --nonblock /run/lock/hringbru-maintenance.lock python3 /opt/hringbru/current/scripts/certificates.py renew'
```

## Firewall and reboot behavior

The deploy helper validates and applies only the `inet hringbru` nftables table. It restricts the application's SIP/media ports to the configured source networks and preserves SSH and other tables. Ansible installs `hringbru-firewall.service` and adds a Docker dependency so the rules load before Docker starts at boot.

An accept in this table does not override a drop in another firewall table. Maintain the corresponding cloud and host allow rules separately. Revalidate the table after other firewall software reloads or replaces rules. Inspect reboot behavior on the actual server before acceptance.

## Private data and backups

The standard private paths are `.env`, `secrets/`, `.local/`, `.preview/`, `deploy/ansible/inventory.yml` and `teams/users.csv`. Keep those paths out of source control. Custom inventory or credential locations need their own exclusions.

Back up the private deployment settings, user mappings, required Docker data and `/opt/hringbru/shared` securely. The shared directory contains ACME account material and certificate private keys; old releases contain trunk passwords. Restrict backup access accordingly.

SIP debug traces can contain phone numbers, SIP digest material and SRTP keys. Redact them before attaching them to public GitHub issues. Check [the acceptance record](acceptance.md) for the remaining live validation work.
