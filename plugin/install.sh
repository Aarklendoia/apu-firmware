#!/bin/sh
# Add the signed os-apufirmware package repository to OPNsense and install the plugin
# (as root). Run it once: the plugin is then listed under System > Firmware > Plugins
# and its updates come with the OPNsense updates (System > Firmware > Updates).
#
#   fetch -o - https://raw.githubusercontent.com/Aarklendoia/apu-firmware/main/plugin/install.sh | sh
#
# Running it again is harmless. It also moves an install made by an older version of this
# script (package added by hand) to the repository.
# Never touches the firmware: firmware updates are started from the web interface.
#
#   APUFW_REPO    GitHub repository of a fork (default: Aarklendoia/apu-firmware); its
#                 GitHub Pages serve the package repository
#   APUFW_PUBKEY  public key file of a fork's repository (default: the key below)
#   APUFW_URL     package repository URL (default: the GitHub Pages of APUFW_REPO), e.g. a
#                 local copy for tests: file:///path/to/repo
set -eu

REPO=${APUFW_REPO:-Aarklendoia/apu-firmware}
PKG=os-apufirmware
NAME=ApuFirmware
CONF=/usr/local/etc/pkg/repos/$NAME.conf
KEY=/usr/local/etc/ssl/$NAME.pub

[ "$(id -u)" -eq 0 ] || { echo "Must be run as root." >&2; exit 1; }

owner=$(echo "${REPO%%/*}" | tr '[:upper:]' '[:lower:]')
URL=${APUFW_URL:-"https://$owner.github.io/${REPO#*/}/pkg"}

# Signing key of the Aarklendoia/apu-firmware package repository. The CI checks that it
# matches the private key the repository is signed with.
# BEGIN PUBLIC KEY
DEFAULT_KEY='-----BEGIN PUBLIC KEY-----
MIICIjANBgkqhkiG9w0BAQEFAAOCAg8AMIICCgKCAgEAybmDJwjsNKwtzw3owI2D
084vc3jrpGskc4PwKV+bZBK5VWlGewrbwgJaFW3bzwKB/+OUglNjiJ1Y/gsVhXtE
veHR9UMVch7MY2DXOxNveJbDyiumjU5mfDG7jqIHKCUljvTk+aSw+h2ZjhB9De94
YnuuJKeXZKRwQljthfHMSO4XmFm7ky0oXW0v4QIR8rgBZ8iyN2/Ey//LGQZM/tcG
8FbdpbZ603lmNTi0Qp9oYeCJmpyP7CQ0uAbaiktSNHRloNO0TG2IPWs6SW6Mhhvv
phZVWY7X1tNSn/kyM88Wa7YjlUoQOS4UBY3NaHdLtlUCpB5J0JNsk5RQizvt+F7Z
bbJgRESKpXDIRAEdh5L56z+OAoDoikmUgB+eOhBfbMYscCSSlt3ujQGXiYNav3le
MlwKv7XKGsm9tUbOYtgiiGKjI0RAOVdr7/+WUAGvPtvPTfIhPIX5UHAm3+8Og0OM
uEUCNmIf4ev2iOHUEqtSxa4qIYxjXM5ZLxwdD8KwIFaf910E8YgDyAlNbLxbVYzT
YfuSYwiOpBxfWwb4tGPVH8Y0mmkjRZUKKe1/B5pyrNzfy7LDjEClUuSddK+UXYE+
dxLGLUQAwK/pjfqCirPIt3+I3qZY/o2ygCtfAXzN+1V7oT4tmgiPafv8LyRuHWhr
rFnDw2VoLv++yDKeGsq7GsMCAwEAAQ==
-----END PUBLIC KEY-----'
# END PUBLIC KEY

mkdir -p "${KEY%/*}"
if [ -n "${APUFW_PUBKEY:-}" ]; then
    cp "$APUFW_PUBKEY" "$KEY"
elif [ "$REPO" = Aarklendoia/apu-firmware ]; then
    printf '%s\n' "$DEFAULT_KEY" > "$KEY"
else
    echo "A fork needs its own key: set APUFW_PUBKEY to its public key file." >&2
    exit 1
fi
chmod 644 "$KEY"

mkdir -p "${CONF%/*}"
cat > "$CONF" <<EOF
$NAME: {
  url: "$URL",
  signature_type: "pubkey",
  pubkey: "$KEY",
  mirror_type: "none",
  enabled: yes
}
EOF
echo "Package repository $NAME: $URL"

pkg update -f -r "$NAME"
version=$(pkg rquery -r "$NAME" %v "$PKG")
[ -n "$version" ] || { echo "$PKG not found in $URL." >&2; exit 1; }
installed=$(pkg query %v "$PKG" 2>/dev/null || true)
from=$(pkg query %R "$PKG" 2>/dev/null || true)

# an install made by hand (pkg add, older install.sh) stays tied to no repository, even
# when reinstalled: remove it first, like "make upgrade" in opnsense/plugins. The settings
# stay in config.xml.
if [ -n "$installed" ] && [ "$from" != "$NAME" ]; then
    echo "Moving $PKG $installed ($from) to the $NAME repository"
    pkg delete -fy "$PKG"
fi

if [ "$(pkg query %v "$PKG" 2>/dev/null || true)" = "$version" ]; then
    echo "$PKG $version is already installed from $NAME."
else
    # -r also limits the dependencies to this repository: flashrom comes from OPNsense's
    pkg info -q flashrom || pkg install -y flashrom
    pkg install -y -r "$NAME" "$PKG"
fi

# record the plugin in config.xml, as System > Firmware > Plugins does, otherwise it is
# listed as "misconfigured"
/usr/local/opnsense/scripts/firmware/register.php install "$PKG"
service configd restart

echo
echo "$PKG ${installed:+$installed -> }$version installed from $NAME."
echo "Configure it under System > Firmware > APU coreboot. Updates come with the OPNsense updates."
