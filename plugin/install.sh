#!/bin/sh
# Install or update the os-apufirmware plugin on OPNsense (as root).
#
#   fetch -o - https://raw.githubusercontent.com/Aarklendoia/apu-firmware/main/plugin/install.sh | sh
#
# Downloads the package from the repository's latest plugin-v* release, installs its
# dependency (flashrom), replaces the installed version and reloads configd.
# Never touches the firmware: firmware updates are started from the web interface.
#
#   APUFW_REPO   GitHub repository (default: Aarklendoia/apu-firmware)
#   APUFW_TOKEN  read-only token, only for a private repository
set -eu

REPO=${APUFW_REPO:-Aarklendoia/apu-firmware}
PKG=os-apufirmware

[ "$(id -u)" -eq 0 ] || { echo "Must be run as root." >&2; exit 1; }

gh_get() {
    if [ -n "${APUFW_TOKEN:-}" ]; then
        curl -fsSL -H "Authorization: Bearer $APUFW_TOKEN" "$@"
    else
        curl -fsSL "$@"
    fi
}

# releases come newest first: take the first plugin-v* one with a .pkg
asset=$(gh_get "https://api.github.com/repos/$REPO/releases?per_page=50" | /usr/local/bin/python3 -c '
import json, sys
for rel in json.load(sys.stdin):
    if rel.get("draft") or not rel.get("tag_name", "").startswith("plugin-v"):
        continue
    for a in rel.get("assets", []):
        if a["name"].endswith(".pkg"):
            print(a["url"], a["name"])
            sys.exit()
')
[ -n "$asset" ] || { echo "No plugin-v* release with a package in $REPO." >&2; exit 1; }
url=${asset% *}
name=${asset#* }
version=${name#"$PKG"-}
version=${version%.pkg}

installed=$(pkg query %v "$PKG" 2>/dev/null || true)
if [ "$installed" = "$version" ]; then
    echo "$PKG $version is already installed."
    exit 0
fi

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
echo "Downloading $name"
gh_get -H 'Accept: application/octet-stream' -o "$tmp/$name" "$url"

pkg info -q flashrom || pkg install -y flashrom
# like "make upgrade" in opnsense/plugins: the settings stay in config.xml
if [ -n "$installed" ]; then
    pkg delete -fy "$PKG"
fi
pkg add "$tmp/$name"
service configd restart

echo
echo "$PKG ${installed:+$installed -> }$version installed."
echo "Configure it under System > Firmware > APU coreboot."
