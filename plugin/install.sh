#!/bin/sh
# Installe ou met à jour le plugin os-apufirmware sur OPNsense (en root).
#
#   fetch -o - https://raw.githubusercontent.com/Aarklendoia/apu-firmware/main/plugin/install.sh | sh
#
# Télécharge le paquet de la dernière release plugin-v* du dépôt, installe sa
# dépendance (flashrom), remplace la version installée et recharge configd.
# Ne touche jamais au firmware : sa mise à jour se lance depuis l'interface.
#
#   APUFW_REPO   dépôt GitHub (défaut : Aarklendoia/apu-firmware)
#   APUFW_TOKEN  jeton en lecture seule, uniquement si le dépôt est privé
set -eu

REPO=${APUFW_REPO:-Aarklendoia/apu-firmware}
PKG=os-apufirmware

[ "$(id -u)" -eq 0 ] || { echo "À lancer en root." >&2; exit 1; }

gh_get() {
    if [ -n "${APUFW_TOKEN:-}" ]; then
        curl -fsSL -H "Authorization: Bearer $APUFW_TOKEN" "$@"
    else
        curl -fsSL "$@"
    fi
}

# releases de la plus récente à la plus ancienne : la première plugin-v* avec un .pkg
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
[ -n "$asset" ] || { echo "Aucune release plugin-v* avec un paquet dans $REPO." >&2; exit 1; }
url=${asset% *}
name=${asset#* }
version=${name#"$PKG"-}
version=${version%.pkg}

installed=$(pkg query %v "$PKG" 2>/dev/null || true)
if [ "$installed" = "$version" ]; then
    echo "$PKG $version est déjà installé."
    exit 0
fi

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
echo "Téléchargement de $name"
gh_get -H 'Accept: application/octet-stream' -o "$tmp/$name" "$url"

pkg info -q flashrom || pkg install -y flashrom
# comme « make upgrade » d'opnsense/plugins : la configuration reste dans config.xml
if [ -n "$installed" ]; then
    pkg delete -fy "$PKG"
fi
pkg add "$tmp/$name"
service configd restart

echo
echo "$PKG ${installed:+$installed -> }$version installé."
echo "Système > Firmware > APU coreboot pour le configurer."
