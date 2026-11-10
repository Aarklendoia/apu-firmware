#!/usr/local/bin/python3

"""
Copyright (C) 2026 Édouard Biton
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice,
   this list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright
   notice, this list of conditions and the following disclaimer in the
   documentation and/or other materials provided with the distribution.

THIS SOFTWARE IS PROVIDED ``AS IS'' AND ANY EXPRESS OR IMPLIED WARRANTIES,
INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY
AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
AUTHOR BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY,
OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
POSSIBILITY OF SUCH DAMAGE.

Coreboot (Dasharo) firmware updates for PC Engines apu2/3/4/6.

    apufw.py status      local state as JSON (no network)
    apufw.py check       query the GitHub releases, update the state, print JSON
    apufw.py flash       download, verify, back up and flash the latest firmware
    apufw.py coldreset   stop services cleanly, then power-cycle the board
    apufw.py log         print the log of the last flash
"""

import fcntl
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

STATE_DIR = os.environ.get('APUFW_STATE_DIR', '/var/db/apufirmware')
CONFIG_XML = os.environ.get('APUFW_CONFIG', '/conf/config.xml')
LOG_FILE = os.environ.get('APUFW_LOG', '/var/log/apufirmware.log')
API = os.environ.get('APUFW_API', 'https://api.github.com')
STATUS_FILE = os.path.join(STATE_DIR, 'status.json')
LOCK_FILE = os.path.join(STATE_DIR, 'lock')
BACKUP_DIR = os.path.join(STATE_DIR, 'backup')
KEEP_BACKUPS = 3

# Cold reset: FullRst (0x0E) written to the FCH reset register (I/O port 0xCF9, also mapped at
# 0xFED803C5): the board is powered off for 3-5 seconds, then boots cold. Method of the PC Engines
# apu2 documentation (cold_reset.md, pfSense), tested on an apu4 with OPNsense 26.7. It does not
# depend on the firmware, unlike the D18F0x6C flag + reboot, which stopped Dasharo UEFI v0.9.1
# right after an update (#33).
# D18F0x6C must read 580ffe00, as on every apu2/3/4/6: a safety check before writing.
COLDRESET_DEV = 'pci0:24:0'
COLDRESET_REG = '0x6c'
COLDRESET_EXPECT = '580ffe00'
FULLRST_MEM = os.environ.get('APUFW_MEM', '/dev/mem')
FULLRST_ADDR = 0xFED803C5
FULLRST_VALUE = b'\x0e'
FULLRST_WAIT = int(os.environ.get('APUFW_FULLRST_WAIT', '15'))


# ------------------------------------------------------------------ helpers

def log(msg):
    line = '%s %s' % (time.strftime('%Y-%m-%d %H:%M:%S'), msg)
    with open(LOG_FILE, 'a') as f:
        f.write(line + '\n')


def run(cmd, logged=False):
    """Run a command, return (returncode, combined output)."""
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              universal_newlines=True)
    except FileNotFoundError:
        return 127, '%s: command not found' % cmd[0]
    if logged:
        log('$ ' + ' '.join(cmd))
        for line in proc.stdout.splitlines():
            log('  ' + line)
    return proc.returncode, proc.stdout.strip()


class M(str):
    """Message shown in the web interface: the English text, which also keeps its template
    and arguments so that the interface can translate it (OPNsense\\ApuFirmware\\Lang).

    M('Download failed: %s', e) == 'Download failed: timeout'. The templates are the msgids
    of plugin/sysutils/apufirmware/lang/*.po.
    """

    def __new__(cls, template, *args):
        args = [a if isinstance(a, (int, float)) else str(a) for a in args]
        self = str.__new__(cls, template % tuple(args) if args else template)
        self.template, self.args = template, args
        return self


MESSAGE_KEYS = ('message', 'check_error', 'blocked_reason')


def with_templates(status):
    """Add <key>_t = [template, args] next to each message, drop the stale ones."""
    for key in MESSAGE_KEYS:
        value = status.get(key)
        if isinstance(value, M):
            status[key + '_t'] = [value.template, value.args]
            continue
        template = status.get(key + '_t')
        try:
            valid = isinstance(template, list) and template[0] % tuple(template[1]) == value
        except (TypeError, ValueError, IndexError):
            valid = False
        if not valid:
            status.pop(key + '_t', None)
    return status


def load_status():
    try:
        with open(STATUS_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_status(status):
    os.makedirs(STATE_DIR, mode=0o700, exist_ok=True)
    tmp = STATUS_FILE + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(with_templates(status), f, indent=2, sort_keys=True)
    os.replace(tmp, STATUS_FILE)


def settings():
    cfg = {'enabled': '1', 'repository': '', 'token': '', 'variant': 'seabios', 'prerelease': '0'}
    try:
        node = ET.parse(CONFIG_XML).getroot().find('./OPNsense/apufirmware/general')
    except (OSError, ET.ParseError):
        node = None
    if node is not None:
        for key in cfg:
            value = node.findtext(key)
            if value is not None:
                cfg[key] = value.strip()
    return cfg


def version_tuple(text):
    """'v24.08.00.01' -> (24, 8, 0, 1); None when no version is found."""
    found = re.findall(r'v?(\d+(?:\.\d+)+)', text or '')
    return tuple(int(x) for x in found[-1].split('.')) if found else None


def version_key(text):
    """Orderable version, suffix included: '26.09.00.01-rc4' < '26.09.00.01'.

    A final release comes after its release candidates (-rcN), which come after a build
    of the release branch (-unreleased). None when no version is found.
    """
    found = re.findall(r'v?(\d+(?:\.\d+)+)(-rc\d+|-unreleased)?', text or '', re.I)
    if not found:
        return None
    number, suffix = found[-1]
    suffix = suffix.lower()
    if suffix.startswith('-rc'):
        stage = (1, int(suffix[3:]))
    elif suffix == '-unreleased':
        stage = (0, 0)
    else:
        stage = (2, 0)
    return tuple(int(x) for x in number.split('.')), stage


def current_key(status):
    """Version key of the running firmware.

    Dasharo SeaBIOS release candidates report the final version in SMBIOS
    (v26.09.00.01 for 26.09.00.01-rc4): the label flashed by this plugin, when it
    has the same number, tells them apart.
    """
    key = version_key(status.get('bios_version'))
    installed = version_key(status.get('installed_label'))
    if key and installed and installed[0] == key[0] and key[1] == (2, 0):
        return installed
    return key


def family(ver):
    """Firmware family of a version tuple: legacy PC Engines, Dasharo SeaBIOS or UEFI."""
    if not ver:
        return None
    if ver[0] == 0:
        return 'uefi'
    if ver[0] == 4:
        return 'legacy'
    return 'seabios'


def hardware():
    _, product = run(['kenv', '-q', 'smbios.system.product'])
    _, bios = run(['kenv', '-q', 'smbios.bios.version'])
    # 'apu4', 'APU4' or 'PC Engines apu4' depending on the firmware
    match = re.search(r'\bapu([2346])\b', product.lower())
    board = 'apu' + match.group(1) if match else None
    current = version_tuple(bios)
    return {
        'board': board,
        'product': product.strip(),
        'bios_version': bios.strip(),
        'current_version': '.'.join(str(x) for x in current) if current else None,
        'current_family': family(current),
        'flashrom_installed': shutil.which('flashrom') is not None,
    }


def flashrom_chip():
    """'-c' arguments when several flashrom chip definitions match the flash.

    The W25Q64 of the apu4 matches both "W25Q64BV/W25Q64CV/W25Q64FV" and "W25Q64JV-.Q",
    and flashrom then refuses to do anything without -c. Same chip ID: take the first one.
    """
    _, out = run(['flashrom', '-p', 'internal'], logged=True)
    match = re.search(r'Multiple flash chip definitions match the detected chip\(s\): (.*)', out)
    names = re.findall(r'"([^"]+)"', match.group(1)) if match else []
    return ['-c', names[0]] if names else []


def write_protection(chip):
    """(start, length, mode) of the flash write protection, length 0 if none."""
    _, out = run(['flashrom', '-p', 'internal', '--wp-status'] + chip, logged=True)
    match = re.search(r'Protection range: start=0x([0-9a-fA-F]+) length=0x([0-9a-fA-F]+)', out)
    mode = re.search(r'Protection mode: (\S+)', out)
    start, length = (int(match.group(1), 16), int(match.group(2), 16)) if match else (0, 0)
    return start, length, mode.group(1) if mode else 'disabled'


def lift_protection(chip, wp):
    """Disable the write protection when the OS may (/WP pin not asserted): True on success.

    Dasharo UEFI protects WP_RO again at every boot, so nothing has to be restored after a
    successful write. On failure, the protection is put back as it was found.
    """
    log('The flash is write protected (0x%x-0x%x, mode %s): lifting the protection to update '
        'WP_RO too' % (wp[0], wp[0] + wp[1] - 1, wp[2]))
    for arg in ('--wp-disable', '--wp-range=0,0'):
        code, _ = run(['flashrom', '-p', 'internal', arg] + chip, logged=True)
        if code != 0:
            break
    now = write_protection(chip)
    if not now[1] or now[2] == 'disabled':
        log('Write protection lifted, the firmware sets it again at the next boot')
        return True
    log('The write protection cannot be lifted (write-protect jumper fitted?)')
    if now != wp:
        restore_protection(chip, wp)
    return False


def restore_protection(chip, wp):
    """Put back the write protection (start, length, mode) found before the update."""
    run(['flashrom', '-p', 'internal', '--wp-range=0x%x,0x%x' % (wp[0], wp[1])] + chip, logged=True)
    if wp[2] != 'disabled':
        run(['flashrom', '-p', 'internal', '--wp-enable'] + chip, logged=True)
    log('Write protection restored (0x%x-0x%x)' % (wp[0], wp[0] + wp[1] - 1))


def fmap(data):
    """FMAP areas of a ROM image as {name: (offset, size)}, empty if there is none."""
    pos = -1
    while True:
        pos = data.find(b'__FMAP__', pos + 1)
        if pos < 0 or pos + 56 > len(data):
            return {}
        major, _, _, size, _, count = struct.unpack_from('<BBQI32sH', data, pos + 8)
        # the signature also appears in the code: check the header
        if major == 1 and size == len(data) and pos + 56 + count * 42 <= len(data):
            break
    areas = {}
    for i in range(count):
        offset, size, name, _ = struct.unpack_from('<II32sH', data, pos + 56 + i * 42)
        areas[name.rstrip(b'\0').decode('ascii', 'replace')] = (offset, size)
    return areas


def fit_protection(plan, wp, rom, backup):
    """Adapt the flashrom arguments to the write protection: (arguments, error)."""
    start, length = wp
    if not length:
        return plan, None
    where = '0x%x-0x%x' % (start, start + length - 1)
    with open(rom, 'rb') as f:
        new = f.read()
    with open(backup, 'rb') as f:
        current = f.read()
    areas = fmap(new)
    if '--fmap' not in plan or not areas:
        return None, M('The flash is write protected (%s) and the whole image must be written: '
                       'disable the protection. Nothing was written.', where)
    names = plan[2::2]          # ['--fmap', '-i', A, '-i', B]
    hit = [n for n in names if n not in areas
           or (areas[n][0] < start + length and start < areas[n][0] + areas[n][1])]
    if not hit:
        return plan, None
    if hit != ['WP_RO'] or 'RW_SECTION_A' not in names:
        return None, M('The flash is write protected (%s) and covers %s: disable the protection. '
                       'Nothing was written.', where, ', '.join(hit))
    # vboot: the read-only part stays and checks RW_SECTION_A with the keys of its GBB
    if fmap(current) != areas or 'GBB' not in areas:
        return None, M('The flash is write protected (%s): only RW_SECTION_A could be written, but the '
                       'flash layout differs. Nothing was written.', where)
    gbb = slice(areas['GBB'][0], areas['GBB'][0] + areas['GBB'][1])
    if current[gbb] != new[gbb]:
        return None, M('The flash is write protected (%s): only RW_SECTION_A could be written, but it is '
                       'signed with other keys than the read-only part (GBB), which would reject it. '
                       'Nothing was written.', where)
    log('The flash is write protected (%s): WP_RO is kept, only RW_SECTION_A is written' % where)
    return ['--fmap', '-i', 'RW_SECTION_A'], None


def flash_plan(hw, variant):
    """Return (flashrom arguments, reason it is blocked or None)."""
    if not hw['board']:
        return None, M('This is not a PC Engines apu2/3/4/6 board.')
    if not hw['flashrom_installed']:
        return None, M('flashrom is not installed.')
    fam = hw['current_family']
    if fam is None:
        return None, M('The current firmware version could not be read.')
    if variant == 'seabios':
        if fam == 'uefi':
            return None, M('Switching from UEFI to SeaBIOS must be done manually.')
        if fam == 'seabios':
            # Dasharo to Dasharo: only the code region, settings (boot order, knobs) are kept
            return ['--fmap', '-i', 'COREBOOT'], None
        # original PC Engines firmware: full image (Dasharo initial deployment)
        return [], None
    if fam != 'uefi':
        return None, M('Switching from SeaBIOS to UEFI must be done manually (OPNsense must boot in UEFI mode).')
    return ['--fmap', '-i', 'WP_RO', '-i', 'RW_SECTION_A'], None


# ------------------------------------------------------------------ GitHub

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _request(url, token, accept, follow=True):
    headers = {'Accept': accept, 'User-Agent': 'os-apufirmware'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    req = urllib.request.Request(url, headers=headers)
    if follow:
        return urllib.request.urlopen(req, timeout=60)
    # asset downloads redirect to a signed URL that rejects the Authorization header
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        return opener.open(req, timeout=60)
    except urllib.error.HTTPError as e:
        if e.code in (301, 302, 303, 307, 308) and e.headers.get('Location'):
            target = urllib.request.Request(e.headers['Location'], headers={'User-Agent': 'os-apufirmware'})
            return urllib.request.urlopen(target, timeout=300)
        raise


def latest_release(cfg, board):
    url = '%s/repos/%s/releases?per_page=50' % (API, cfg['repository'])
    with _request(url, cfg['token'], 'application/vnd.github+json') as resp:
        releases = json.load(resp)

    prefix = cfg['variant'] + '-v'
    best = None
    for rel in releases:
        tag = rel.get('tag_name', '')
        if rel.get('draft') or not tag.startswith(prefix):
            continue
        if rel.get('prerelease') and cfg['prerelease'] != '1':
            continue
        label = tag[len(prefix):]                       # 24.08.00.01[-rcN|-unreleased]
        key = version_key(label)
        if not key:
            continue
        rom_name = 'pcengines_%s_%s_v%s.rom' % (board, cfg['variant'], label)
        assets = {a['name']: a for a in rel.get('assets', [])}
        if rom_name not in assets or 'SHA256SUMS' not in assets:
            continue
        if best is None or key > best['key']:
            best = {'ver': key[0], 'key': key, 'label': label, 'tag': tag, 'url': rel.get('html_url'),
                    'rom': assets[rom_name], 'sums': assets['SHA256SUMS']}
    return best


def download(asset, token, dest):
    with _request(asset['url'], token, 'application/octet-stream', follow=False) as resp, \
            open(dest, 'wb') as out:
        shutil.copyfileobj(resp, out)


# ------------------------------------------------------------------ commands

def cmd_status():
    status = load_status()
    hw = hardware()
    status.update(hw)
    # the variant may have changed since the last check: block right away
    status['variant'] = settings()['variant']
    _, status['blocked_reason'] = flash_plan(status, status['variant'])
    # after the cold reset, the new firmware reports the pending version: done
    if status.get('needs_coldreset') and status.get('pending_version') == hw['current_version']:
        status['needs_coldreset'] = False
        status['pending_version'] = None
        status['installed_label'] = status.pop('pending_label', None)
        status['state'] = 'idle'
        status['message'] = M('Firmware %s is running.', hw['bios_version'])
        save_status(status)
    latest = version_key(status.get('latest_label') or status.get('latest_version'))
    if hw['current_version'] and latest:
        status['update_available'] = (
            latest > current_key(status) and not status.get('needs_coldreset', False))
    status.setdefault('state', 'idle')
    if status['state'] == 'running' and not _locked():
        status['state'] = 'error'
        status['message'] = M('The last operation was interrupted, see the log.')
        save_status(status)
    return status


def _locked():
    """True while a flash holds the lock."""
    if not os.path.exists(LOCK_FILE):
        return False
    try:
        with open(LOCK_FILE, 'w') as f:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(f, fcntl.LOCK_UN)
        return False
    except OSError:
        return True


def cmd_check():
    status = cmd_status()
    cfg = settings()
    status['checked_at'] = int(time.time())
    status['check_error'] = None
    status['variant'] = cfg['variant']
    _, status['blocked_reason'] = flash_plan(status, cfg['variant'])

    if not cfg['repository']:
        status['check_error'] = M('No GitHub repository configured.')
    elif not status['board']:
        status['check_error'] = M('Unsupported board.')
    else:
        try:
            rel = latest_release(cfg, status['board'])
        except (urllib.error.URLError, OSError, ValueError) as e:
            rel = None
            status['check_error'] = M('GitHub query failed: %s', e)
        if rel:
            status['latest_version'] = '.'.join(str(x) for x in rel['ver'])
            status['latest_label'] = rel['label']
            status['latest_tag'] = rel['tag']
            status['latest_url'] = rel['url']
            status['update_available'] = (
                status['current_version'] is not None
                and rel['key'] > current_key(status)
                and not status.get('needs_coldreset', False))
        elif not status['check_error']:
            status['check_error'] = M('No release for %s (%s) in %s.', status['board'], cfg['variant'],
                                      cfg['repository'])
    save_status(status)
    return status


def _fail(status, message):
    log('ERROR: ' + message)
    status['state'] = 'error'
    status['message'] = message
    save_status(status)
    return 1


def cmd_flash():
    try:
        return _flash()
    except Exception as e:      # never leave the state stuck on "running"
        return _fail(load_status(), M('Unexpected error: %s', e))


def _flash():
    os.makedirs(STATE_DIR, mode=0o700, exist_ok=True)
    lock = open(LOCK_FILE, 'w')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print('A firmware operation is already running.')
        return 1

    open(LOG_FILE, 'w').close()
    status = cmd_check()
    cfg = settings()
    status['state'] = 'running'
    status['message'] = M('Preparing')
    save_status(status)
    log('Board %s, firmware %s' % (status['product'], status['bios_version']))

    plan, blocked = flash_plan(status, cfg['variant'])
    if blocked:
        return _fail(status, blocked)
    if status.get('check_error'):
        return _fail(status, status['check_error'])
    if not status.get('update_available'):
        return _fail(status, M('No newer firmware is available.'))

    rel = latest_release(cfg, status['board'])
    work = os.path.join(STATE_DIR, 'download')
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work, mode=0o700)
    rom = os.path.join(work, rel['rom']['name'])
    sums = os.path.join(work, 'SHA256SUMS')

    status['message'] = M('Downloading %s', rel['rom']['name'])
    save_status(status)
    log(status['message'])
    try:
        download(rel['rom'], cfg['token'], rom)
        download(rel['sums'], cfg['token'], sums)
    except (urllib.error.URLError, OSError) as e:
        return _fail(status, M('Download failed: %s', e))

    expected = None
    with open(sums) as f:
        for line in f:
            parts = line.split()
            if len(parts) == 2 and parts[1].lstrip('*') == rel['rom']['name']:
                expected = parts[0].lower()
    with open(rom, 'rb') as f:
        actual = hashlib.sha256(f.read()).hexdigest()
    if not expected or expected != actual:
        return _fail(status, M('Checksum mismatch for %s (expected %s, got %s).', rel['rom']['name'],
                               expected, actual))
    log('sha256 OK: %s' % actual)

    status['message'] = M('Backing up the current firmware')
    save_status(status)
    os.makedirs(BACKUP_DIR, mode=0o700, exist_ok=True)
    backup = os.path.join(BACKUP_DIR, '%s_%s_%s.rom' % (
        status['board'], status['bios_version'].replace(' ', '_') or 'unknown',
        time.strftime('%Y%m%d-%H%M%S')))
    chip = flashrom_chip()
    code, _ = run(['flashrom', '-p', 'internal', '-r', backup] + chip, logged=True)
    if code != 0:
        return _fail(status, M('Backup failed, nothing was written. Is the firmware write protection disabled?'))
    backups = sorted((os.path.join(BACKUP_DIR, b) for b in os.listdir(BACKUP_DIR)), key=os.path.getmtime)
    for old in backups[:-KEEP_BACKUPS]:
        os.unlink(old)
    status['last_backup'] = backup

    wp = write_protection(chip)
    lifted = None
    if wp[1] and wp[2] != 'disabled' and status['current_family'] == 'uefi' and lift_protection(chip, wp):
        lifted = wp
    plan, error = fit_protection(plan, (0, 0) if lifted else wp[:2], rom, backup)
    if error:
        return _fail(status, error)

    status['message'] = M('Writing %s', rel['rom']['name'])
    save_status(status)
    code, out = run(['flashrom', '-p', 'internal', '-w', rom] + plan + chip, logged=True)
    if code != 0:
        if 'Erasing and writing flash chip' in out:
            return _fail(status, M('flashrom failed while writing. DO NOT REBOOT: retry the update, '
                                   'or restore %s with flashrom.', backup))
        if lifted:
            restore_protection(chip, lifted)
        return _fail(status, M('flashrom failed before writing, the firmware is unchanged.'))

    status['state'] = 'flashed'
    status['needs_coldreset'] = True
    status['update_available'] = False
    status['pending_version'] = status['latest_version']
    status['pending_label'] = rel['label']
    status['message'] = M('Firmware %s written and verified. A cold reset is required.', rel['label'])
    save_status(status)
    log(status['message'])
    return 0


def cmd_coldreset():
    code, value = run(['pciconf', '-r', COLDRESET_DEV, COLDRESET_REG])
    if code != 0 or value.strip().lower() != COLDRESET_EXPECT:
        log('Cold reset refused: %s %s reads "%s", expected %s' % (
            COLDRESET_DEV, COLDRESET_REG, value, COLDRESET_EXPECT))
        print('Unexpected register value, cold reset refused. Unplug the power instead.')
        return 1
    log('Cold reset: stopping services')
    run(['/usr/local/etc/rc.syshook', 'stop'], logged=True)
    run(['sync'])
    time.sleep(2)
    run(['sync'])
    log('Cold reset: power cycling the board')
    os.sync()
    try:
        with open(FULLRST_MEM, 'r+b', buffering=0) as mem:
            mem.seek(FULLRST_ADDR)
            mem.write(FULLRST_VALUE)
    except OSError as e:
        log('Cold reset failed: %s. Unplug the power for a few seconds.' % e)
        print('Cold reset failed, unplug the power instead.')
        return 1
    time.sleep(FULLRST_WAIT)
    log('Cold reset had no effect. Unplug the power for a few seconds.')
    print('Cold reset had no effect, unplug the power instead.')
    return 1


def main():
    action = sys.argv[1] if len(sys.argv) > 1 else 'status'
    if action == 'status':
        print(json.dumps(with_templates(cmd_status())))
    elif action == 'check':
        print(json.dumps(with_templates(cmd_check())))
    elif action == 'flash':
        return cmd_flash()
    elif action == 'coldreset':
        return cmd_coldreset()
    elif action == 'log':
        try:
            with open(LOG_FILE) as f:
                print(f.read())
        except OSError:
            pass
    else:
        print(__doc__.split('Coreboot')[1])
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
