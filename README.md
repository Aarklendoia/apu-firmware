# apu-firmware

**Dasharo (coreboot)** firmware for PC Engines apu2/3/4/6, built automatically from 3mdeb's public
sources, and an **OPNsense** plugin to install it from the web interface.

- **CI**: every Monday, GitHub Actions checks whether 3mdeb has published a new version. If so, the
  ROM is built and published as a release of this repository (ROM, `SHA256SUMS`, source
  traceability).
- **`os-apufirmware` plugin**: checks this repository's releases every night, announces new
  versions in the OPNsense notification bell, and offers an *Update firmware* button. It downloads
  the ROM, verifies its sha256, backs up the current firmware, flashes, verifies the write, then
  offers a *Cold reset* button to start the new firmware remotely.

```
.github/workflows/firmware.yml   version check + build + release
.github/workflows/plugin.yml     plugin lint, tests, package and release
scripts/apu-fw                   firmware build (also usable locally, see its header)
plugin/sysutils/apufirmware/     OPNsense plugin sources
plugin/install.sh                plugin installation on the router
plugin/tests/                    backend tests (fake flashrom, fake GitHub)
```

## Firmware builds

The *Firmware* workflow runs every Monday, or manually from **Actions** → *Firmware* →
*Run workflow*. A build takes about 5 minutes: the coreboot toolchain comes prebuilt in 3mdeb's
`dasharo-sdk` container image.

Only apu4 is built by default. To build other boards, set the repository variable `APU_BOARDS`
(Settings → Secrets and variables → Actions → Variables), for example `["apu2","apu4"]`.

The ROMs are GPL binaries. Each release includes a `.sources` file listing the exact public commits
used: coreboot, Dasharo patch queue, submodules and toolchain image.

### Comparison with the official 3mdeb builds

3mdeb only sells the official ROMs, but publishes their signed sha256. Every build compares its ROM
with that hash, after checking the signature chain up to the Dasharo master key, and the release
notes say whether the ROM is **identical** to the official one or **differs**. To check a ROM
without rebuilding:

```sh
scripts/apu-fw verify pcengines_apu4_seabios_v24.08.00.01.rom 24.08.00.01
```

Known difference: the v24.08.00.01 SeaBIOS ROM differs from the official build although every
input matches 3mdeb's SBOM, and the build is deterministic (same hash with `git am` or `guilt`,
GitHub or upstream mirrors). See [#5](https://github.com/Aarklendoia/apu-firmware/issues/5).

Manual runs of the *Firmware* workflow accept `patch_tool`, `mirrors` and `publish` (off = test
build kept as a workflow artifact, no release) to investigate such differences.

## Installing the plugin on OPNsense

Over SSH on the router, as root:

```sh
fetch -o - https://raw.githubusercontent.com/Aarklendoia/apu-firmware/main/plugin/install.sh | sh
```

The script downloads the `os-apufirmware` package from the latest `plugin-v*` release, installs
`flashrom`, replaces the installed version and reloads configd. Run the same command to update the
plugin. Optional variables: `APUFW_REPO=<owner>/<repo>` for a fork, `APUFW_TOKEN=<token>` for a
private repository.

Then open **System → Firmware → APU coreboot**, enter the repository (`Aarklendoia/apu-firmware`)
and save. A check starts immediately. The access token is only needed for a private repository:
use a fine-grained token limited to that repository with **Contents: Read-only**.

### Plugin versions

Plugin versions are managed by [release-please](https://github.com/googleapis/release-please).
PR titles follow [Conventional Commits](https://www.conventionalcommits.org/) (a required check
enforces it), and squash merges use them as commit messages. release-please keeps a release PR
open that groups the plugin changes merged since the last release: `fix` bumps the patch version,
`feat` the minor version, `!` (breaking change) the major version. Merging that PR updates the
changelog and `PLUGIN_VERSION`, tags `plugin-vX.Y.Z` and publishes the release, then the package
is built in a FreeBSD VM and attached to it.

The release PR is opened with the `RELEASE_PLEASE_TOKEN` secret, a fine-grained personal access
token for this repository only with **Contents** and **Pull requests** in read and write: a PR
opened with the default Actions token would not run the required checks.

These releases never install anything by themselves, and the firmware is never flashed without a
click in the web interface.

## Updating the firmware

1. A notification appears in the bell when a new version is published.
2. *APU coreboot* page → **Update firmware**. The log scrolls during the operation (~1 minute).
3. **Cold reset**: services are stopped cleanly, then the board is powered off for a few seconds
   and boots the new firmware. A regular `reboot` is not enough on an APU.

What the plugin does, in order (any failure before step 4 leaves the flash untouched):
1. download from the release, sha256 verification;
2. backup of the current flash to `/var/db/apufirmware/backup/` (last 3 kept);
3. checks: apu2/3/4/6 board detected, same variant as the installed firmware;
4. `flashrom` write, then verification. From a Dasharo firmware, only the `COREBOOT` region is
   written (boot order and settings are kept). From the original PC Engines firmware (v4.x), the
   full image is written, as recommended by 3mdeb, and settings return to their defaults.

## Before the first update

- **F10** menu at boot (sortbootorder): firmware write protection must be disabled. This should be
  the last time the serial console is needed.
- The write-protect jumper (J3 on apu4) must not be fitted.
- Keep a serial console at hand for this first time. Later updates can be done remotely.

## Troubleshooting

- **Failure before writing** (download, sha256, write protection): nothing was changed, and the
  message says so.
- **Failure while writing**: the message says **DO NOT REBOOT**. Retry the update, or restore the
  backup over SSH:
  `flashrom -p internal -w /var/db/apufirmware/backup/<file>.rom`
- **The board no longer boots**: only an external SPI programmer (e.g. CH341A with a SOIC8 clip,
  or the PC Engines spi1a) can write the backup back.

## Translation

The interface strings are in English, like the rest of OPNsense, and go through `gettext`
(`lang._()` in the view), so they can be translated.

## Known limitations

- Switching between SeaBIOS and UEFI is not supported from the interface (OPNsense must be
  reinstalled in UEFI mode). Stay on SeaBIOS unless you have a specific need.
- The plugin has been tested with a fake `flashrom` and a fake GitHub (`plugin/tests/`), not on
  real hardware before its first use.
- `-unreleased` builds (*Pre-releases* option) come from branches that 3mdeb has not validated.
