# apu-firmware

Firmware **Dasharo (coreboot)** pour PC Engines apu2/3/4/6, compilé automatiquement depuis les
sources publiques de 3mdeb, et plugin **OPNsense** pour l'installer depuis l'interface web.

- **CI** : chaque lundi, GitHub Actions regarde si 3mdeb a publié une nouvelle version. Si c'est
  le cas, la ROM est compilée et publiée comme release du dépôt (ROM, `SHA256SUMS`, traçabilité
  des sources).
- **Plugin `os-apufirmware`** : vérifie chaque nuit les releases du dépôt, annonce les
  nouvelles versions dans la cloche de notifications d'OPNsense, et propose un bouton
  *Update firmware*. Il télécharge la ROM, vérifie le sha256, sauvegarde le firmware actuel,
  flashe, vérifie l'écriture, puis propose un bouton *Cold reset* pour démarrer le nouveau
  firmware à distance.

```
.github/workflows/firmware.yml   veille + compilation + release
.github/workflows/plugin.yml     lint, tests, paquet et release du plugin
scripts/apu-fw                   compilation (utilisable aussi en local, cf. en-tête)
plugin/sysutils/apufirmware/     sources du plugin OPNsense
plugin/install.sh                installation du plugin sur le routeur
plugin/tests/                    tests du backend (faux flashrom, faux GitHub)
```

## 1. Mise en place du dépôt

Créer un dépôt **privé** `apu-firmware` sur GitHub, puis :

```bash
git remote add origin git@github.com:<toi>/apu-firmware.git
git push -u origin main
```

Pourquoi privé : les ROMs compilées sont des binaires GPL que 3mdeb vend sous abonnement. Dans un
dépôt privé, tu ne redistribues rien et la question ne se pose pas. Si tu le rends public, tu dois
fournir les sources correspondantes (les fichiers `.sources` de chaque release pointent vers les
commits exacts).

Premier build : onglet **Actions** → *Firmware* → *Run workflow*. Ensuite, c'est automatique tous
les lundis. Compter 20 à 40 minutes pour une compilation. La consommation reste très en dessous
du quota gratuit d'un dépôt privé : une vérification par semaine, une compilation par version.

Pour compiler d'autres cartes, crée la variable de dépôt `APU_BOARDS` (Settings → Secrets and
variables → Actions → Variables), par exemple `["apu2","apu4"]`. Par défaut : `["apu4"]`.

## 2. Jeton pour le routeur (dépôt privé)

GitHub → Settings → Developer settings → **Fine-grained tokens** :
- *Repository access* : uniquement `apu-firmware`
- *Permissions* : **Contents : Read-only** (rien d'autre)
- expiration : à ton choix (le plugin signalera une erreur de vérification à l'expiration)

## 3. Installation du plugin sur OPNsense

En SSH sur le routeur, en root :

```sh
fetch -o - https://raw.githubusercontent.com/<toi>/apu-firmware/main/plugin/install.sh | sh
```

Le script télécharge le paquet `os-apufirmware` de la dernière release `plugin-v*`, installe
`flashrom`, remplace la version déjà installée puis recharge configd. La même commande sert aux
mises à jour du plugin. Variables facultatives : `APUFW_REPO=<toi>/apu-firmware` pour un autre
dépôt, `APUFW_TOKEN=<jeton>` si le dépôt est privé (le script se télécharge alors depuis un clone).

Versions du plugin : à chaque fusion sur `main` qui modifie `plugin/sysutils/`, la CI compile le
paquet dans une VM FreeBSD et publie une release `plugin-vX.Y.Z`. `X.Y` vient de `PLUGIN_VERSION`
dans le `Makefile` du plugin (à changer à la main pour une évolution notable), `Z` est incrémenté
automatiquement. Ces releases n'installent rien d'elles-mêmes, et le firmware n'est jamais flashé
sans un clic dans l'interface.

Configuration : **Système → Firmware → APU coreboot**. Renseigner `<toi>/apu-firmware` et le
jeton, enregistrer. Une vérification se lance immédiatement.

## 4. Mise à jour du firmware

1. Une notification apparaît dans la cloche quand une nouvelle version est publiée.
2. Page *APU coreboot* → **Update firmware**. Le journal défile pendant l'opération (~1 minute).
3. **Cold reset** : les services sont arrêtés proprement, puis la carte est coupée quelques
   secondes et redémarre sur le nouveau firmware. Un `reboot` classique ne suffit pas sur APU.

Ce que fait le plugin, dans l'ordre (tout arrêt avant l'étape 4 laisse la flash intacte) :
1. téléchargement depuis la release, vérification sha256 ;
2. sauvegarde de la flash actuelle dans `/var/db/apufirmware/backup/` (3 dernières gardées) ;
3. contrôle : carte apu2/3/4/6 reconnue, même variante que le firmware installé ;
4. écriture par `flashrom` puis vérification. Depuis un firmware Dasharo, seule la région
   `COREBOOT` est écrite (ordre de boot et réglages conservés). Depuis le firmware PC Engines
   d'origine (v4.x), l'image complète est écrite, comme le préconise 3mdeb, et les réglages
   reviennent aux valeurs par défaut.

## Avant la première mise à jour

- Menu **F10** au démarrage (sortbootorder) : la protection en écriture du firmware doit être
  désactivée. C'est la dernière fois que la tablette en RS232 sert, normalement.
- Le cavalier de protection d'écriture (J3 sur apu4) ne doit pas être posé.
- Garder une console série à portée pour cette première fois. Les suivantes se font à distance.

## En cas de problème

- **Échec avant l'écriture** (téléchargement, sha256, protection en écriture) : rien n'a été
  modifié, le message l'indique.
- **Échec pendant l'écriture** : le message dit **NE PAS REDÉMARRER**. Relancer la mise à jour,
  ou restaurer la sauvegarde en SSH :
  `flashrom -p internal -w /var/db/apufirmware/backup/<fichier>.rom`
- **La carte ne démarre plus** : seul un programmeur SPI externe (ex. CH341A + pince SOIC8, ou
  le spi1a de PC Engines) permet de réécrire la sauvegarde.

## Traduction

Les textes de l'interface sont en anglais, comme dans tout OPNsense, et passent par
`gettext` (`lang._()` dans la vue). Ils restent en anglais tant qu'ils ne sont pas traduits.

## Limites connues

- Le passage SeaBIOS ↔ UEFI n'est pas géré depuis l'interface (OPNsense doit être réinstallé en
  mode UEFI). Rester en SeaBIOS sauf besoin précis.
- Le plugin a été testé avec un faux `flashrom` et un faux GitHub (`plugin/tests/`), pas sur
  du matériel réel avant sa première utilisation.
- Les builds `-unreleased` (option *Pré-versions*) viennent de branches que 3mdeb n'a pas
  validées.
