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

`current` is promoted only after the container passes its health check. Named Docker volumes preserve Asterisk's writable data across container replacement. Image builds run on the server. Old release directories are retained and contain private settings and passwords; archive or remove them according to your retention policy, preserving the active release and shared state.

## Routine changes

Edit the workstation's private `.env`, inventory or password file, then run:

```sh
./bin/deploy
```

An unchanged healthy release avoids a restart. Changes that require container replacement are refused when active channels are detected; retry after calls finish. A second check runs just before replacement. This is not seamless call migration: plan a quiet maintenance window for changed releases.

A failed health check exits with an error and does not promote the success marker. It does not automatically restore the previous container or shared configuration. Inspect the failure and retry or redeploy the intended source/configuration. The old `current` symlink alone is not evidence that the old container is still serving calls.

If the first deployment fails, `current` does not exist yet. Inspect the container directly from your workstation:

```sh
ssh -t YOUR_SSH_TARGET 'sudo docker logs --tail=100 hringbru-asterisk-1'
```

Certificate installation and configuration rendering explicitly set permissions on mounted children for container UID 10001, even with the server's private umask. The parent shared directory remains root-only. Redeployment repairs the mounted TLS permissions using the existing valid certificate.

Previous manual `/opt/hringbru/.local` deployments require explicit migration of their private data to the shared layout. The playbook refuses to abandon that directory silently.

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
