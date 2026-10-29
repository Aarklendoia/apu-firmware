"""End-to-end tests of apufw.py with fake kenv/flashrom/pciconf and a fake GitHub API."""
import hashlib
import http.server
import json
import os
import shutil
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
SEEN = []          # (path, authorization header) of every request
BAD_SUMS = {'on': False}


class Fake(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        SEEN.append((self.path, self.headers.get('Authorization')))
        base = 'http://127.0.0.1:%d' % self.server.server_port
        if self.path.startswith('/repos/me/apu-firmware/releases'):
            rels = [
                {'tag_name': 'seabios-v24.05.00.01', 'draft': False, 'prerelease': False,
                 'html_url': 'x', 'assets': [
                     {'name': 'pcengines_apu4_seabios_v24.05.00.01.rom', 'url': base + '/asset/old'},
                     {'name': 'SHA256SUMS', 'url': base + '/asset/oldsums'}]},
                {'tag_name': 'seabios-v24.08.00.01', 'draft': False, 'prerelease': False,
                 'html_url': 'https://github.com/me/apu-firmware/releases/tag/seabios-v24.08.00.01',
                 'assets': [{'name': ROM_NAME, 'url': base + '/asset/rom'},
                            {'name': 'SHA256SUMS', 'url': base + '/asset/sums'}]},
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
            if what == 'rom':
                return self._send(ROM)
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


def hw(product, bios, flash_ok=True, write_fail=False, reg='580ffe00', ambiguous=False):
    fake('kenv', 'case "$2" in smbios.system.product) echo "%s";; smbios.bios.version) echo "%s";; esac\n'
         % (product, bios))
    # like flashrom on an apu4 W25Q64: without -c, it stops after probing
    fake('flashrom', 'echo "flashrom $*" >> %s/flashrom.calls\n'
         'case "$*" in *" -c "*) ;; *) %s;; esac\n'
         'case "$*" in "-p internal") ;; *" -r "*) %s;;\n'
         '  *" -w "*) echo "Erasing and writing flash chip... "; %s;; esac\n'
         % (ROOT, AMBIGUOUS if ambiguous else ':',
            'printf x > "$4"' if flash_ok else 'echo "write protected"; exit 1',
            'echo FAILED; exit 1' if write_fail else 'echo "Erase/write done."; echo "Verifying flash... VERIFIED."'))
    fake('pciconf', 'echo "pciconf $*" >> %s/pciconf.calls\n[ "$1" = "-r" ] && echo %s\nexit 0\n' % (ROOT, reg))


def config(repo='me/apu-firmware', token='', variant='seabios', pre='0'):
    with open(os.path.join(ROOT, 'config.xml'), 'w') as f:
        f.write('<opnsense><OPNsense><apufirmware><general><enabled>1</enabled>'
                '<repository>%s</repository><token>%s</token><variant>%s</variant>'
                '<prerelease>%s</prerelease></general></apufirmware></OPNsense></opnsense>'
                % (repo, token, variant, pre))


def reset_state():
    shutil.rmtree(os.path.join(ROOT, 'state'), ignore_errors=True)
    for f in ('flashrom.calls', 'pciconf.calls'):
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

# 10. pre-releases when enabled
reset_state()
config(pre='1')
hw('apu4', 'v24.08.00.01')
_, st = run('check')
check('prerelease: offered when enabled', st.get('latest_label') == '25.09.00.01-unreleased', st)
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

print('\n%d failure(s)' % len(FAILS))
sys.exit(1 if FAILS else 0)
