#!/bin/sh
# Installe ou met à jour le plugin os-apufirmware sur OPNsense (en root).
#
#   sh plugin/install.sh            depuis un clone de ce dépôt sur le routeur
#
# Construit le paquet dans l'arborescence officielle opnsense/plugins (Mk/plugins.mk),
# l'installe avec ses dépendances (flashrom) et recharge configd.
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
TREE=${PLUGINS_TREE:-/usr/plugins}

[ "$(id -u)" -eq 0 ] || { echo "À lancer en root." >&2; exit 1; }
command -v git >/dev/null || pkg install -y git

if [ -d "$TREE/.git" ]; then
    git -C "$TREE" pull -q --ff-only
else
    git clone -q --depth 1 https://github.com/opnsense/plugins "$TREE"
fi

rm -rf "$TREE/sysutils/apufirmware"
cp -R "$HERE/sysutils/apufirmware" "$TREE/sysutils/apufirmware"
make -C "$TREE/sysutils/apufirmware" upgrade
service configd restart

echo
echo "Installé. Système > Firmware > APU coreboot pour le configurer."
