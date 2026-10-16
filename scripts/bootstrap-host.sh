#!/bin/sh
# One-time minimal-host bootstrap through an existing administrator / su session.
set -eu
if [ "$(id -u)" != 0 ]; then echo 'Bootstrap requires an existing root shell.' >&2; exit 1; fi
if [ "$#" != 1 ]; then echo 'Usage: bootstrap-host.sh EXISTING_SSH_USER' >&2; exit 1; fi
deploy_account=$1
id "$deploy_account" >/dev/null
. /etc/os-release
case "$ID" in
    debian) [ "${VERSION_ID%%.*}" = 13 ] || { echo 'Debian 13 is required.' >&2; exit 1; } ;;
    ubuntu) [ "${VERSION_ID%%.*}" -ge 24 ] || { echo 'Ubuntu 24.04+ is required.' >&2; exit 1; } ;;
    *) echo 'Only Debian 13 and Ubuntu 24.04+ are supported.' >&2; exit 1 ;;
esac
changed=0
set --
for package in sudo python3 python3-apt ca-certificates openssl nftables util-linux iproute2; do
    state=$(dpkg-query -W -f='${db:Status-Status}' "$package" 2>/dev/null || true)
    if [ "$state" != installed ]; then set -- "$@" "$package"; fi
done
if [ "$#" -gt 0 ]; then
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "$@"
    changed=1
fi
if [ "$(id -u "$deploy_account")" != 0 ]; then
    case " $(id -nG "$deploy_account") " in
        *' sudo '*) ;;
        *) /usr/sbin/usermod -aG sudo "$deploy_account"; changed=1 ;;
    esac
fi
/usr/sbin/visudo -c
/usr/bin/sudo -l -U "$deploy_account" >/dev/null
printf 'HRINGBRU_BOOTSTRAP_CHANGED=%s\n' "$changed"
printf 'Base prerequisites are installed. Reconnect SSH, then deploy through sudo.\n'
