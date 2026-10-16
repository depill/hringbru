# Manual deployment reference

Normal deployments use [`./bin/deploy`](../README.md#quick-start). Run the commands below from the repository root on the server. The following commands describe the original manual layout for troubleshooting; do not copy its renewal units over an Ansible-managed installation.

Copy this directory to `/opt/hringbru` (or change the systemd unit's path). Install Docker from [Docker's official instructions](https://docs.docker.com/engine/install/), including the Compose plugin. Install host dependencies:

```sh
sudo apt-get update
sudo apt-get install -y python3 openssl nftables
cd /opt/hringbru
cp .env.example .env
cp teams/users.example.csv teams/users.csv
```

Edit `.env` with the real deployment details. It uses literal `KEY=value`, without quotes, variable expansion or inline comments. Carrier source ranges are mandatory and are never guessed from one DNS lookup. Deployment-specific values are intentionally blank in `.env.example` and must be filled before validation.

Set the SIP password using a local hidden prompt, then render:

```sh
sudo python3 scripts/set-password.py
sudo python3 scripts/render.py
sudo docker compose build asterisk
```

The password lives in `secrets/hringdu_password`, not an environment variable or image layer. Rendered configuration and private keys live below the private `.local/` directory. Files inside its mounted child directories are readable by the unprivileged container UID 10001. Do not make `.local` world-accessible. `.env`, `.local`, secrets and the actual user mapping are git-ignored and excluded from image builds.

## Network access

Review generated `.local/hringbru.nft`. It restricts only the application's inbound SIP/media ports and preserves other tables and SSH. It uses `destroy table` (nftables >= 1.0.9) so repeat application replaces only its own table. Host/cloud default-deny rules must also permit the allowed traffic; an accept in this table does not override another table's drop.

| Direction | Source/destination | Destination on server / remote |
|---|---|---|
| Inbound SIP | Confirmed Hringdu signalling ranges | 5060 UDP by default, or configured TCP |
| Outbound SIP | `centrex.hringdu.is` | 5060, configured transport |
| Inbound TLS | Microsoft `52.112.0.0/14`, `52.120.0.0/14` | 5061 TCP |
| Outbound TLS | `sip.pstnhub.microsoft.com`, `sip2...`, `sip3...` | 5061 TCP |
| Inbound RTP/SRTP | Confirmed Hringdu media ranges and both Microsoft ranges | UDP 10000–10199 by default |
| Outbound media | Hringdu media ranges | Carrier-specified UDP range |
| Outbound SRTP | Microsoft media ranges | UDP 3478–3481 and 49152–53247 |
| Inbound ACME | Internet / Let's Encrypt validation | TCP 80, while standalone certbot runs |
| Outbound infrastructure | DNS resolvers, time service, HTTPS registries/ACME/package servers | DNS, NTP and HTTPS as appropriate |

For a dedicated host, after reviewing the generated rules:

```sh
sudo nft -c -f .local/hringbru.nft
sudo nft -f .local/hringbru.nft
```

Persist that table through your host firewall manager (for a dedicated nftables host, include its file from `/etc/nftables.conf` and enable `nftables.service`). Do not replace an existing firewall configuration wholesale. The manual commands do not apply firewall rules automatically; the Ansible deployment validates and applies the dedicated application table before starting Asterisk. Restrict SIP 5060 to Hringdu and TLS 5061 to Teams **before** exposing Asterisk. Media bypass and IPv6 are disabled in this setup. Recheck [Microsoft's network requirements](https://learn.microsoft.com/en-us/microsoftteams/direct-routing-plan) at deployment.

## Let's Encrypt and startup

The SBC A record must already resolve to this server. Port 80 must be reachable and unused during issuance/renewal. Do not publish an AAAA record unless you also provide working ACME IPv6 access. For hosts already running a web server, adapt certificate issuance to webroot/DNS validation first; standalone certbot cannot share port 80.

```sh
sudo python3 scripts/certificates.py issue
sudo docker compose up -d asterisk
sudo docker compose ps
sudo docker compose logs --tail=100 asterisk
```

The certificate must match `SBC_FQDN`; the installer checks the hostname, remaining lifetime and key match. Full chains are installed, and peer verification remains enabled. The base image uses the OS CA trust bundle; the smoke test checks the Microsoft 2017 and DigiCert roots specified in Microsoft's current guidance.

[Let's Encrypt removed client-authentication EKU support in 2026](https://letsencrypt.org/2025/05/14/ending-tls-client-authentication/). [Microsoft currently accepts SBC certificates without that EKU](https://learn.microsoft.com/en-us/microsoftteams/direct-routing-whats-new#clarification-on-client-authentication-extended-key-usage-eku). This is not a guarantee for every TLS stack or future policy: prove TLS/OPTIONS with the issued certificate, and revalidate after renewal. An alternate CA's suitable SBC certificate can be installed at `.local/tls/fullchain.pem` and `.local/tls/privkey.pem` if required; the startup script requires a certificate valid for at least another 24 hours.

Enable automated renewal **after** a successful test:

```sh
sudo python3 scripts/certificates.py renew --dry-run
sudo cp deploy/hringbru-renew.service deploy/hringbru-renew.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now hringbru-renew.timer
sudo systemctl list-timers hringbru-renew.timer
```

Renewal runs twice daily, installs only changed files and requests `core restart gracefully`: new calls stop while existing calls drain, then Asterisk restarts and establishes fresh TLS sessions. A single bridge has a brief outage during restart. Monitor `journalctl -u hringbru-renew.service`, certificate expiry, container health, Hringdu registration and Teams SBC health. A healthy Docker process is not proof of trunk health. Certificate automation requires root on this installation because certbot writes root-owned ACME files.
