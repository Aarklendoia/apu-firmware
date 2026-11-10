"""End-to-end tests of apufw.py with fake kenv/flashrom/pciconf and a fake GitHub API."""
import hashlib
import http.server
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import threading

SCRIPT = sys.argv[1]
ROOT = tempfile.mkdtemp()
BIN = os.path.join(ROOT, 'bin')
os.makedirs(BIN)

ROM = b'\xff' * 1024 + b'NEWFW'
ROM_NAME = 'pcengines_apu4_seabios_v24.08.00.01.rom'


def fmap_rom(gbb, fill):
    """Small Dasharo UEFI image: RW_SECTION_A in the lower half, WP_RO (FMAP, GBB) in the upper half."""
    areas = [('RW_SECTION_A', 0x1000, 0x3000), ('WP_RO', 0x4000, 0x4000), ('FMAP', 0x4000, 0x400),
             ('GBB', 0x4400, 0x100), ('COREBOOT', 0x4500, 0x3b00)]
    data = bytearray(fill * 0x8000)
    hdr = b'__FMAP__' + struct.pack('<BBQI32sH', 1, 1, 0, len(data), b'FLASH', len(areas))
    hdr += b''.join(struct.pack('<II32sH', off, size, name.encode(), 0) for name, off, size in areas)
    data[0x4000:0x4000 + len(hdr)] = hdr
    data[0x4400:0x4500] = gbb.ljust(0x100, b'\0')
    return bytes(data)


UEFI_ROM = fmap_rom(b'$GBB key 1', b'\x01')
UEFI_NAME = 'pcengines_apu4_uefi_v0.9.1.rom'
SEEN = []          # (path, authorization header) of every request
BAD_SUMS = {'on': False}
EXTRA = []         # releases added by a test, served before the others (newest first, like GitHub)


def rc_release(tag, board='apu4', prerelease=True):
    """Release whose ROM is served by the generic /asset/rom:<name> endpoint."""
    variant, label = tag.split('-v', 1)
    name = 'pcengines_%s_%s_v%s.rom' % (board, variant, label)
    return {'tag_name': tag, 'draft': False, 'prerelease': prerelease, 'html_url': tag,
            'assets': [{'name': name, 'url': '/asset/rom:' + name},
                       {'name': 'SHA256SUMS', 'url': '/asset/sums:' + name}]}


class Fake(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        SEEN.append((self.path, self.headers.get('Authorization')))
        base = 'http://127.0.0.1:%d' % self.server.server_port
        if self.path.startswith('/repos/me/apu-firmware/releases'):
            rels = [dict(r, assets=[dict(a, url=base + a['url']) for a in r['assets']]) for r in EXTRA] + [
                {'tag_name': 'seabios-v24.05.00.01', 'draft': False, 'prerelease': False,
                 'html_url': 'x', 'assets': [
                     {'name': 'pcengines_apu4_seabios_v24.05.00.01.rom', 'url': base + '/asset/old'},
                     {'name': 'SHA256SUMS', 'url': base + '/asset/oldsums'}]},
                {'tag_name': 'seabios-v24.08.00.01', 'draft': False, 'prerelease': False,
                 'html_url': 'https://github.com/me/apu-firmware/releases/tag/seabios-v24.08.00.01',
                 'assets': [{'name': ROM_NAME, 'url': base + '/asset/rom'},
                            {'name': 'SHA256SUMS', 'url': base + '/asset/sums'}]},
                {'tag_name': 'uefi-v0.9.1', 'draft': False, 'prerelease': False, 'html_url': 'z',
                 'assets': [{'name': UEFI_NAME, 'url': base + '/asset/uefirom'},
                            {'name': 'SHA256SUMS', 'url': base + '/asset/uefisums'}]},
                rc_release('seabios-v24.08.00.01-rc2'),
                {'tag_name': 'seabios-v25.09.00.01-unreleased', 'draft': False, 'prerelease': True,
                 'html_url': 'y', 'assets': [
                     {'name': 'pcengines_apu4_seabios_v25.09.00.01-unreleased.rom', 'url': base + '/asset/pre'},
                     {'name': 'SHA256SUMS', 'url': base + '/asset/presums'}]},
            ]
            return self._send(json.dumps(rels).encode())
        if self.path.startswith('/asset/'):
            # like GitHub: redirect to a signed URL that rejects Authorization
            self.send_response(302)
            self.send_header('Location', base + '/signed' + self.path[len('/asset'):])
            self.end_headers()
            return
        if self.path.startswith('/signed/'):
            if self.headers.get('Authorization'):
                self.send_response(400)
                self.end_headers()
                return
            what = self.path[len('/signed/'):]
            if what.startswith('rom:'):
                return self._send(ROM)
            if what.startswith('sums:'):
                return self._send(('%s  %s\n' % (hashlib.sha256(ROM).hexdigest(), what[5:])).encode())
            if what == 'rom':
                return self._send(ROM)
            if what == 'uefirom':
                return self._send(UEFI_ROM)
            if what == 'uefisums':
                return self._send(('%s  %s\n' % (hashlib.sha256(UEFI_ROM).hexdigest(), UEFI_NAME)).encode())
            if what == 'sums':
                h = '0' * 64 if BAD_SUMS['on'] else hashlib.sha256(ROM).hexdigest()
                return self._send(('%s  %s\n' % (h, ROM_NAME)).encode())
        self.send_response(404)
        self.end_headers()

    def _send(self, body):
        self.send_response(200)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


srv = http.server.HTTPServer(('127.0.0.1', 0), Fake)
threading.Thread(target=srv.serve_forever, daemon=True).start()


def fake(name, body):
    p = os.path.join(BIN, name)
    with open(p, 'w') as f:
        f.write('#!/bin/sh\n' + body)
    os.chmod(p, 0o755)


AMBIGUOUS = ('echo \'Multiple flash chip definitions match the detected chip(s): '
             '"W25Q64BV/W25Q64CV/W25Q64FV", "W25Q64JV-.Q"\'; exit 1')


def hw(product, bios, flash_ok=True, write_fail=False, reg='580ffe00', ambiguous=False, wp=(0, 0), flash=b'x',
       wp_locked=False):
    """Fake board: wp is the protected (start, length), flash the content read by flashrom -r.

    The protection is kept in a state file; wp_locked is the /WP pin (jumper fitted): it cannot be
    changed. A write touching WP_RO fails before writing while the protection is on.
    write_fail: True fails while writing, 'before' fails before anything is written.
    """
    fake('kenv', 'case "$2" in smbios.system.product) echo "%s";; smbios.bios.version) echo "%s";; esac\n'
         % (product, bios))
    with open(os.path.join(ROOT, 'flash.rom'), 'wb') as f:
        f.write(flash)
    with open(os.path.join(ROOT, 'wp'), 'w') as f:
        f.write('%d %d %s\n' % (wp[0], wp[1], 'hardware' if wp[1] else 'disabled'))
    locked = 'echo "Failed to apply new WP settings"; exit 1' if wp_locked else ':'
    if write_fail == 'before':
        write = 'echo "Error: unexpected chip state"; exit 1'
    elif write_fail:
        write = 'echo "Erasing and writing flash chip... "; echo FAILED; exit 1'
    else:
        write = 'echo "Erasing and writing flash chip... Erase/write done."; echo "Verifying flash... VERIFIED."'
    # like flashrom on an apu4 W25Q64: without -c, it stops after probing
    fake('flashrom', 'echo "flashrom $*" >> {R}/flashrom.calls\n'
         'read s l m < {R}/wp\n'
         'case "$*" in *" -c "*) ;; *) {amb};; esac\n'
         'case "$*" in "-p internal") ;;\n'
         '  *--wp-status*) printf "Protection range: start=0x%08x length=0x%08x\\nProtection mode: %s\\n" '
         '"$s" "$l" "$m";;\n'
         '  *--wp-disable*) {locked}; echo "$s $l disabled" > {R}/wp;;\n'
         '  *--wp-enable*) {locked}; echo "$s $l hardware" > {R}/wp;;\n'
         '  *--wp-range=*) {locked}; r=${{*#*--wp-range=}}; r=${{r%% *}}; '
         'echo "$((${{r%,*}})) $((${{r#*,}})) $m" > {R}/wp;;\n'
         '  *" -r "*) {read};;\n'
         '  *" -w "*WP_RO*) [ "$l" != 0 ] && [ "$m" != disabled ] && {{ echo "write protected"; exit 1; }}; '
         '{write};;\n'
         '  *" -w "*) {write};; esac\n'.format(
             R=ROOT, amb=AMBIGUOUS if ambiguous else ':', locked=locked, write=write,
             read='cp %s/flash.rom "$4"' % ROOT if flash_ok else 'echo "write protected"; exit 1'))
    fake('pciconf', 'echo "pciconf $*" >> %s/pciconf.calls\n[ "$1" = "-r" ] && echo %s\nexit 0\n' % (ROOT, reg))


def config(repo='me/apu-firmware', token='', variant='seabios', pre='0'):
    with open(os.path.join(ROOT, 'config.xml'), 'w') as f:
        f.write('<opnsense><OPNsense><apufirmware><general><enabled>1</enabled>'
                '<repository>%s</repository><token>%s</token><variant>%s</variant>'
                '<prerelease>%s</prerelease></general></apufirmware></OPNsense></opnsense>'
                % (repo, token, variant, pre))


def reset_state():
    shutil.rmtree(os.path.join(ROOT, 'state'), ignore_errors=True)
    for f in ('flashrom.calls', 'pciconf.calls', 'wp'):
        if os.path.exists(os.path.join(ROOT, f)):
            os.unlink(os.path.join(ROOT, f))


def calls(name):
    p = os.path.join(ROOT, name)
    return open(p).read() if os.path.exists(p) else ''


env = dict(os.environ, PATH=BIN + ':' + os.environ['PATH'],
           APUFW_STATE_DIR=os.path.join(ROOT, 'state'), APUFW_CONFIG=os.path.join(ROOT, 'config.xml'),
           APUFW_LOG=os.path.join(ROOT, 'apufw.log'),
           APUFW_API='http://127.0.0.1:%d' % srv.server_port)


def run(*args):
    p = subprocess.run([sys.executable, SCRIPT] + list(args), env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
    if p.stderr:
        print(p.stderr)
    out = p.stdout.strip()
    try:
        return p.returncode, json.loads(out)
    except ValueError:
        return p.returncode, out


FAILS = []


def check(name, cond, detail=''):
    print(('PASS ' if cond else 'FAIL ') + name + ('' if cond else '  -> %s' % detail))
    if not cond:
        FAILS.append(name)


# 1. not an APU
reset_state()
config()
hw('', '')
_, st = run('status')
check('non-APU: no board', st['board'] is None, st)
_, st = run('check')
check('non-APU: check refuses', st['check_error'] == 'Unsupported board.', st)

# 1b. before any check: blocked reason known, nothing checked
reset_state()
config()
hw('apu4', 'Dasharo (coreboot+UEFI) v0.9.0')
_, st = run('status')
check('status: not checked yet', not st.get('checked_at'), st)
check('status: variant mismatch blocked without a check', 'manually' in (st.get('blocked_reason') or ''), st)
check('status: blocked reason template', st.get('blocked_reason_t', [''])[0] == st.get('blocked_reason'), st)

# 2. legacy PC Engines v4.19.0.1 -> 24.08 available, full flash
reset_state()
config()
hw('apu4', 'v4.19.0.1')
_, st = run('check')
check('legacy: latest is 24.08 (prerelease ignored)', st.get('latest_label') == '24.08.00.01', st)
check('legacy: update available', st.get('update_available') is True, st)
check('legacy: not blocked', st.get('blocked_reason') is None, st)

# 3. flash
code, _ = run('flash')
_, st = run('status')
fl = calls('flashrom.calls')
check('flash: exit 0', code == 0, open(env['APUFW_LOG']).read())
check('flash: state flashed + cold reset needed', st['state'] == 'flashed' and st['needs_coldreset'], st)
rw = [line for line in fl.splitlines() if ' -r ' in line or ' -w ' in line]
check('flash: backup taken first', rw[0].startswith('flashrom -p internal -r '), fl)
check('flash: legacy -> full image (no --fmap)', '-w' in fl and '--fmap' not in fl, fl)
check('flash: backup file exists', os.path.exists(st.get('last_backup', '')), st)
check('flash: no update offered until reset', st['update_available'] is False, st)
check('download: no Authorization sent to signed URL',
      all(a is None for p, a in SEEN if p.startswith('/signed/')), SEEN)

# 4. after the cold reset the new version runs
hw('apu4', 'v24.08.00.01')
_, st = run('status')
check('after reboot: cold reset flag cleared', not st['needs_coldreset'] and st['state'] == 'idle', st)
check('after reboot: up to date', st['update_available'] is False, st)

# 5. Dasharo 24.05 -> 24.08 keeps settings
reset_state()
hw('apu4', 'v24.05.00.01')
run('check')
code, _ = run('flash')
check('dasharo: --fmap -i COREBOOT', '--fmap -i COREBOOT' in calls('flashrom.calls'), calls('flashrom.calls'))

# 5b. several chip definitions match (apu4 W25Q64): -c on every read and write
reset_state()
hw('apu4', 'v24.05.00.01', ambiguous=True)
code, _ = run('flash')
fl = calls('flashrom.calls')
check('ambiguous chip: flash succeeds', code == 0, open(env['APUFW_LOG']).read())
check('ambiguous chip: -c on read and write',
      all('-c W25Q64BV/W25Q64CV/W25Q64FV' in line for line in fl.splitlines() if ' -r ' in line or ' -w ' in line), fl)

# 6. checksum mismatch -> nothing written
reset_state()
hw('apu4', 'v24.05.00.01')
BAD_SUMS['on'] = True
code, _ = run('flash')
_, st = run('status')
check('bad sha256: error', code == 1 and st['state'] == 'error' and 'Checksum' in st['message'], st)
check('bad sha256: flashrom never called', calls('flashrom.calls') == '', calls('flashrom.calls'))
tpl = st.get('message_t') or ['', []]
check('message template: kept for translation',
      tpl[0] == 'Checksum mismatch for %s (expected %s, got %s).' and tpl[0] % tuple(tpl[1]) == st['message'], st)
BAD_SUMS['on'] = False

# 7. write protected: backup fails -> nothing written
reset_state()
hw('apu4', 'v24.05.00.01', flash_ok=False)
code, _ = run('flash')
_, st = run('status')
check('write protect: refused before writing', st['state'] == 'error' and 'Backup failed' in st['message'], st)
check('write protect: no -w', ' -w ' not in calls('flashrom.calls'), calls('flashrom.calls'))

# 8. write failure -> do not reboot
reset_state()
hw('apu4', 'v24.05.00.01', write_fail=True)
code, _ = run('flash')
_, st = run('status')
check('write failure: DO NOT REBOOT', 'DO NOT REBOOT' in st['message'], st)

# 9. UEFI firmware with SeaBIOS selected -> blocked
reset_state()
hw('apu4', 'Dasharo (coreboot+UEFI) v0.9.1')
_, st = run('check')
check('uefi installed + seabios variant: blocked', 'manually' in (st.get('blocked_reason') or ''), st)
code, _ = run('flash')
check('uefi installed: flash refused', code == 1 and calls('flashrom.calls') == '', calls('flashrom.calls'))

# 9a. UEFI, upper half write protected, protection can be lifted (apu4, no jumper): WP_RO too
UPPER = (0x4000, 0x4000)
reset_state()
config(variant='uefi')
hw('apu4', 'Dasharo (coreboot+UEFI) v0.9.0', wp=UPPER, flash=fmap_rom(b'$GBB key 1', b'\x00'))
code, _ = run('flash')
fr = calls('flashrom.calls')
check('uefi + WP lifted: flash succeeds', code == 0, open(env['APUFW_LOG']).read())
check('uefi + WP lifted: protection disabled first', '--wp-disable' in fr and '--wp-range=0,0' in fr, fr)
check('uefi + WP lifted: WP_RO and RW_SECTION_A', '-w ' in fr and '--fmap -i WP_RO -i RW_SECTION_A' in fr, fr)
check('uefi + WP lifted: not restored after success (firmware does it)', '--wp-enable' not in fr, fr)

# 9a2. same, but flashrom fails before writing: protection put back as found
reset_state()
hw('apu4', 'Dasharo (coreboot+UEFI) v0.9.0', wp=UPPER, flash=fmap_rom(b'$GBB key 1', b'\x00'),
   write_fail='before')
code, _ = run('flash')
_, st = run('status')
check('uefi + WP lifted, write refused: error', code == 1 and 'unchanged' in st['message'], st)
check('uefi + WP lifted, write refused: protection restored',
      open(os.path.join(ROOT, 'wp')).read().split() == ['16384', '16384', 'hardware'],
      calls('flashrom.calls'))

# 9b. UEFI, upper half write protected by the jumper: only RW_SECTION_A
reset_state()
hw('apu4', 'Dasharo (coreboot+UEFI) v0.9.0', wp=UPPER, flash=fmap_rom(b'$GBB key 1', b'\x00'), wp_locked=True)
code, _ = run('flash')
writes = [line for line in calls('flashrom.calls').splitlines() if ' -w ' in line]
check('uefi + WP: flash succeeds', code == 0, open(env['APUFW_LOG']).read())
check('uefi + WP: only RW_SECTION_A written',
      len(writes) == 1 and writes[0].endswith('--fmap -i RW_SECTION_A'), writes)

# 9c. same, but the read-only part has other keys: refused before writing
reset_state()
hw('apu4', 'Dasharo (coreboot+UEFI) v0.9.0', wp=UPPER, flash=fmap_rom(b'$GBB key 2', b'\x00'), wp_locked=True)
code, _ = run('flash')
_, st = run('status')
check('uefi + WP, other keys: refused', code == 1 and 'other keys' in st['message'], st)
check('uefi + WP, other keys: no -w', ' -w ' not in calls('flashrom.calls'), calls('flashrom.calls'))

# 9d. UEFI without protection: WP_RO and RW_SECTION_A
reset_state()
hw('apu4', 'Dasharo (coreboot+UEFI) v0.9.0', flash=fmap_rom(b'$GBB key 2', b'\x00'))
code, _ = run('flash')
check('uefi, no WP: WP_RO and RW_SECTION_A', '--fmap -i WP_RO -i RW_SECTION_A' in calls('flashrom.calls'),
      calls('flashrom.calls'))
config()

# 9e. legacy firmware (full image) with protection: refused
reset_state()
hw('apu4', 'v4.19.0.1', wp=(0x400000, 0x400000))
code, _ = run('flash')
_, st = run('status')
check('full image + WP: refused', code == 1 and 'write protected' in st['message'], st)
check('full image + WP: no -w', ' -w ' not in calls('flashrom.calls'), calls('flashrom.calls'))

# 10. pre-releases when enabled
reset_state()
config(pre='1')
hw('apu4', 'v24.08.00.01')
_, st = run('check')
check('prerelease: offered when enabled', st.get('latest_label') == '25.09.00.01-unreleased', st)
config()

# 10a. version order: final > rcN > rc(N-1) > unreleased build of the release branch
sys.path.insert(0, os.path.dirname(os.path.abspath(SCRIPT)))
import apufw  # noqa: E402
order = ['24.08.00.01-unreleased', '24.08.00.01-rc1', '24.08.00.01-rc2', '24.08.00.01-rc10', '24.08.00.01',
         '25.09.00.01-unreleased', '26.09.00.01-rc4', '26.09.00.01']
keys = [apufw.version_key(v) for v in order]
check('version order with suffixes', keys == sorted(keys), list(zip(order, keys)))
check('version key from SMBIOS (UEFI rc)',
      apufw.version_key('Dasharo (coreboot+UEFI) v0.9.1-rc3') == ((0, 9, 1), (1, 3)),
      apufw.version_key('Dasharo (coreboot+UEFI) v0.9.1-rc3'))

# 10b. an rc older than the installed final release is never offered
reset_state()
config(pre='1')
hw('apu4', 'v24.08.00.01')
_, st = run('check')
check('rc of the installed version: not offered', st.get('latest_label') != '24.08.00.01-rc2', st)

# 10c. SeaBIOS rc: flashed, reported as the final version in SMBIOS, final offered later
EXTRA[:] = [rc_release('seabios-v26.09.00.01-rc4')]
reset_state()
hw('apu4', 'v24.08.00.01')
_, st = run('check')
check('rc: newest rc offered', st.get('latest_label') == '26.09.00.01-rc4' and st['update_available'], st)
code, _ = run('flash')
check('rc: flash succeeds', code == 0, open(env['APUFW_LOG']).read())
hw('apu4', 'v26.09.00.01')                      # what a 26.09.00.01-rc4 SeaBIOS reports
_, st = run('status')
check('rc: cold reset done, rc label remembered',
      not st['needs_coldreset'] and st.get('installed_label') == '26.09.00.01-rc4', st)
check('rc: up to date while no final exists', st['update_available'] is False, st)
EXTRA.insert(0, rc_release('seabios-v26.09.00.01', prerelease=False))
_, st = run('check')
check('rc: final offered over the installed rc',
      st.get('latest_label') == '26.09.00.01' and st['update_available'] is True, st)
config()                                        # release candidates off: the final is still offered
_, st = run('check')
check('rc: final offered with release candidates off',
      st.get('latest_label') == '26.09.00.01' and st['update_available'] is True, st)
code, _ = run('flash')
_, st = run('status')
check('rc -> final: cold reset done, final label', st.get('installed_label') == '26.09.00.01', st)
check('rc -> final: up to date', st['update_available'] is False, st)

# 10d. rc not offered when release candidates are off
reset_state()
EXTRA[:] = [rc_release('seabios-v26.09.00.01-rc4')]
hw('apu4', 'v24.08.00.01')
_, st = run('check')
check('rc off: rc ignored', st.get('latest_label') == '24.08.00.01' and st['update_available'] is False, st)

# 10e. UEFI rc reports its suffix: the final release is offered
reset_state()
EXTRA[:] = []
config(variant='uefi')
hw('apu4', 'Dasharo (coreboot+UEFI) v0.9.1-rc3')
_, st = run('check')
check('uefi rc3 -> final 0.9.1 offered', st.get('latest_label') == '0.9.1' and st['update_available'] is True, st)
config()

# 11. private repo token sent to API only
reset_state()
SEEN.clear()
config(token='github_pat_TEST123')
hw('apu4', 'v24.05.00.01')
run('flash')
api = [a for p, a in SEEN if p.startswith('/repos/') or p.startswith('/asset/')]
signed = [a for p, a in SEEN if p.startswith('/signed/')]
check('token: sent to API and asset endpoints', api and all(a == 'Bearer github_pat_TEST123' for a in api), SEEN)
check('token: not leaked to signed URLs', signed and all(a is None for a in signed), SEEN)
config()

# 12. cold reset safety
reset_state()
hw('apu4', 'v24.08.00.01', reg='12345678')
code, _ = run('coldreset')
check('coldreset: refused on unexpected register', code == 1 and '-w' not in calls('pciconf.calls'),
      calls('pciconf.calls'))
reset_state()
hw('apu4', 'v24.08.00.01')
fake('rc.syshook', 'exit 0\n')
code, _ = run('coldreset')
check('coldreset: writes 0x580ffe10', 'pciconf -w pci0:24:0 0x6c 0x580ffe10' in calls('pciconf.calls'),
      calls('pciconf.calls'))

# 13. interrupted run is detected
reset_state()
hw('apu4', 'v24.05.00.01')
os.makedirs(env['APUFW_STATE_DIR'])
with open(os.path.join(env['APUFW_STATE_DIR'], 'status.json'), 'w') as f:
    json.dump({'state': 'running'}, f)
_, st = run('status')
check('stale running state -> error', st['state'] == 'error' and 'interrupted' in st['message'], st)

# 14. a template that no longer gives the message is dropped
reset_state()
hw('apu4', 'v24.05.00.01')
os.makedirs(env['APUFW_STATE_DIR'])
with open(os.path.join(env['APUFW_STATE_DIR'], 'status.json'), 'w') as f:
    json.dump({'state': 'idle', 'message': 'Changed by hand', 'message_t': ['Downloading %s', ['x.rom']]}, f)
_, st = run('status')
check('stale template dropped', st.get('message') == 'Changed by hand' and 'message_t' not in st, st)

print('\n%d failure(s)' % len(FAILS))
sys.exit(1 if FAILS else 0)
