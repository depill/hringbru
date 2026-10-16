#!/bin/sh
# Read-only host audit. Uses the base shell; no sudo or Python is required.
set -u
printf 'SSH account: '; id
printf '\nOperating system:\n'; cat /etc/os-release
printf '\nArchitecture: '; uname -m
printf 'Init: '; cat /proc/1/comm
printf 'CPU count: '; getconf _NPROCESSORS_ONLN
printf '\nPrerequisite packages:\n'
for package in sudo python3 python3-apt ca-certificates openssl nftables util-linux iproute2 docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin; do
    state=$(dpkg-query -W -f='${db:Status-Status}' "$package" 2>/dev/null || true)
    if [ "$state" = installed ]; then printf '  %-24s installed\n' "$package"; else printf '  %-24s missing\n' "$package"; fi
done
printf '\nPython: '; if command -v python3 >/dev/null 2>&1; then python3 --version; else printf 'missing\n'; fi
printf 'Sudo access: '
if command -v sudo >/dev/null 2>&1; then
    if sudo -n true >/dev/null 2>&1; then printf 'noninteractive sudo works\n'; else printf 'password or policy check required\n'; fi
else printf 'sudo executable missing\n'; fi
printf '\nDisk:\n'; df -h / /var /opt
printf '\nMemory:\n'; head -3 /proc/meminfo
printf '\nNetwork interfaces:\n'
if command -v ip >/dev/null 2>&1; then ip -brief address | cat; fi
printf '\nListening TCP/UDP ports:\n'
if command -v ss >/dev/null 2>&1; then ss -ltnu; fi
printf '\nClock (UTC): '; date -u +%Y-%m-%dT%H:%M:%SZ
printf 'NTP synchronized: '
if command -v timedatectl >/dev/null 2>&1; then timedatectl show -p NTPSynchronized --value 2>/dev/null || true; fi
printf '\nCA bundle: '
if [ -s /etc/ssl/certs/ca-certificates.crt ]; then printf 'present\n'; else printf 'missing\n'; fi
printf '\nThis audit does not install packages, change access, or validate live SIP/Teams connectivity.\n'
