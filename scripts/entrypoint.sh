#!/bin/sh
set -eu
for file in /etc/asterisk/pjsip.conf /etc/asterisk/extensions.conf /tls/fullchain.pem /tls/privkey.pem; do
    test -r "$file" || { echo "Missing or unreadable $file; render configuration and install certificates first." >&2; exit 1; }
done
openssl x509 -in /tls/fullchain.pem -noout -checkend 86400 >/dev/null || {
    echo 'Certificate expires in less than 24 hours; renew before starting.' >&2; exit 1;
}
exec /usr/sbin/asterisk -f -C /etc/asterisk/asterisk.conf
