#!/usr/bin/env python3
"""Translations of the os-apufirmware plugin (gettext catalogue "apufirmware").

    python3 -I plugin/lang.py extract           print the template (.pot) of every string
    python3 -I plugin/lang.py update            add the new strings to each .po (empty)
    python3 -I plugin/lang.py check             every string translated, same placeholders
    python3 -I plugin/lang.py build DESTDIR     compile the .po into DESTDIR/<lang>/LC_MESSAGES

Strings come from the views ({{ apuLang._('...') }}), the PHP code (Lang::_('...')), the
form definitions (<label>, <help>, <hint>) and the backend messages (M('...') in apufw.py).
Translations: plugin/sysutils/apufirmware/lang/<lang>.po, e.g. fr_FR.po.
"""
import ast
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.join(HERE, 'sysutils', 'apufirmware')
SRC = os.path.join(PLUGIN, 'src')
LANG_DIR = os.path.join(PLUGIN, 'lang')
DOMAIN = 'apufirmware'

QUOTED = r"'((?:[^'\\]|\\.)*)'"
PATTERNS = {
    '.volt': re.compile(r"apuLang\._\(\s*" + QUOTED + r"\s*\)"),
    '.php': re.compile(r"Lang::_\(\s*" + QUOTED + r"\s*\)"),
}
FORM_TAGS = re.compile(r"<(label|help|hint)>([^<]+)</\1>")
PLACEHOLDER = re.compile(r"%(?:\d+\$)?[-+ 0#]*\d*(?:\.\d+)?[sdxXf%]")


def unquote(text):
    return re.sub(r"\\(.)", r"\1", text)


def python_messages(path):
    """First argument of every M(...) call: implicit concatenation is resolved by ast."""
    tree = ast.parse(open(path, encoding='utf-8').read(), path)
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'M'
                and node.args and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)):
            yield node.args[0].value, node.lineno


def extract():
    """{msgid: [locations]}, in source order."""
    found = {}
    for root, _, files in sorted(os.walk(SRC)):
        for name in sorted(files):
            path = os.path.join(root, name)
            rel = os.path.relpath(path, HERE)
            ext = os.path.splitext(name)[1]
            if ext == '.py':
                items = sorted(python_messages(path), key=lambda item: item[1])
            elif ext in PATTERNS or (ext == '.xml' and os.sep + 'forms' + os.sep in path):
                text = open(path, encoding='utf-8').read()
                if ext == '.xml':
                    matches = [(m.group(2).strip(), m.start(2)) for m in FORM_TAGS.finditer(text)]
                else:
                    matches = [(unquote(m.group(1)), m.start(1)) for m in PATTERNS[ext].finditer(text)]
                items = [(s, text.count('\n', 0, pos) + 1) for s, pos in matches]
            else:
                continue
            for msgid, line in items:
                if msgid:
                    found.setdefault(msgid, []).append('%s:%d' % (rel, line))
    return found


def po_quote(text):
    return '"' + text.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n') + '"'


def po_unquote(text):
    return re.sub(r'\\(.)', lambda m: {'n': '\n', 't': '\t'}.get(m.group(1), m.group(1)), text)


def read_po(path):
    """{msgid: msgstr} of a .po file (single and multi-line strings, no plurals)."""
    entries, key, field = {}, None, None
    msgid = msgstr = ''

    def flush():
        if key is not None:
            entries[msgid] = msgstr

    for line in open(path, encoding='utf-8'):
        line = line.strip()
        if line.startswith('msgid '):
            flush()
            key, field = True, 'id'
            msgid, msgstr = po_unquote(line[7:-1]), ''
        elif line.startswith('msgstr '):
            field = 'str'
            msgstr = po_unquote(line[8:-1])
        elif line.startswith('"') and key is not None:
            if field == 'id':
                msgid += po_unquote(line[1:-1])
            else:
                msgstr += po_unquote(line[1:-1])
    flush()
    entries.pop('', None)         # header
    return entries


def template(found):
    out = ['msgid ""', 'msgstr ""', '"Content-Type: text/plain; charset=UTF-8\\n"', '']
    for msgid, where in found.items():
        out.append('#: ' + ' '.join(where))
        if PLACEHOLDER.search(msgid):
            out.append('#, c-format')
        out += ['msgid ' + po_quote(msgid), 'msgstr ""', '']
    return '\n'.join(out)


def po_files():
    return sorted(os.path.join(LANG_DIR, f) for f in os.listdir(LANG_DIR) if f.endswith('.po'))


def cmd_update(found):
    for path in po_files():
        known = read_po(path)
        new = [m for m in found if m not in known]
        if new:
            with open(path, 'a', encoding='utf-8') as f:
                for msgid in new:
                    f.write('\n#: %s\nmsgid %s\nmsgstr ""\n' % (' '.join(found[msgid]), po_quote(msgid)))
        print('%s: %d new string(s)' % (os.path.basename(path), len(new)))


def cmd_check(found):
    errors = []
    for path in po_files():
        name = os.path.basename(path)
        po = read_po(path)
        for msgid, where in found.items():
            if not po.get(msgid):
                errors.append('%s: not translated: %r (%s)' % (name, msgid, where[0]))
            elif PLACEHOLDER.findall(msgid) != PLACEHOLDER.findall(po[msgid]):
                errors.append('%s: placeholders differ: %r -> %r' % (name, msgid, po[msgid]))
        for msgid in po:
            if msgid not in found:
                errors.append('%s: obsolete string: %r' % (name, msgid))
    for e in errors:
        print(e)
    print('%d string(s), %d catalogue(s), %d error(s)' % (len(found), len(po_files()), len(errors)))
    return 1 if errors else 0


def cmd_build(destdir):
    for path in po_files():
        lang = os.path.splitext(os.path.basename(path))[0]
        out = os.path.join(destdir, lang, 'LC_MESSAGES')
        os.makedirs(out, exist_ok=True)
        subprocess.run(['msgfmt', '--check', '-o', os.path.join(out, DOMAIN + '.mo'), path], check=True)
        print(os.path.join(out, DOMAIN + '.mo'))


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ''
    if cmd == 'extract':
        print(template(extract()))
    elif cmd == 'update':
        cmd_update(extract())
    elif cmd == 'check':
        return cmd_check(extract())
    elif cmd == 'build' and len(sys.argv) == 3:
        cmd_build(sys.argv[2])
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
