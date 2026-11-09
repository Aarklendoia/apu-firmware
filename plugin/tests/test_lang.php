<?php

/*
 * Lang (plugin catalogue, fallback, backend message templates) with the compiled French
 * catalogue: php plugin/tests/test_lang.php <locale directory built by plugin/lang.py>
 * Needs the fr_FR.UTF-8 locale.
 */

require __DIR__ . '/../sysutils/apufirmware/src/opnsense/mvc/app/library/OPNsense/ApuFirmware/Lang.php';

use OPNsense\ApuFirmware\Lang;

$fails = 0;
function check($name, $cond, $detail = '')
{
    global $fails;
    echo ($cond ? 'PASS ' : 'FAIL ') . $name . ($cond ? '' : '  -> ' . var_export($detail, true)) . "\n";
    $fails += $cond ? 0 : 1;
}

if (setlocale(LC_ALL, 'fr_FR.UTF-8') === false) {
    echo "fr_FR.UTF-8 locale missing\n";
    exit(2);
}
Lang::$directory = $argv[1];

check('plain string', Lang::_('Installed') === 'Installé', Lang::_('Installed'));
check('unknown string: English', Lang::_('Not in any catalogue') === 'Not in any catalogue');
check('empty string', Lang::_('') === '');

$st = Lang::status([
    'message' => 'Downloading x.rom',
    'message_t' => ['Downloading %s', ['x.rom']],
    'check_error' => 'GitHub query failed: timeout',
    'check_error_t' => ['GitHub query failed: %s', ['timeout']],
    'blocked_reason' => 'Edited by hand',
    'blocked_reason_t' => ['Unsupported board.', []],
    'state' => 'running',
]);
check('message translated', $st['message'] === 'Téléchargement de x.rom', $st);
check('check_error translated', $st['check_error'] === 'Échec de la requête GitHub : timeout', $st);
check('template not matching: message kept', $st['blocked_reason'] === 'Edited by hand', $st);
check('templates removed', !isset($st['message_t']) && !isset($st['check_error_t']) && !isset($st['blocked_reason_t']), $st);
check('other fields untouched', $st['state'] === 'running', $st);

$st = Lang::status(['message' => 'Range 0x10-0x1f', 'message_t' => ['Range 0x%x-0x%x', [16, 31]]]);
check('untranslated template: English message', $st['message'] === 'Range 0x10-0x1f', $st);
$st = Lang::status(['message' => 'x', 'message_t' => 'not a template']);
check('malformed template ignored', $st === ['message' => 'x'], $st);

$form = Lang::within(function () {
    return gettext('Daily check');
});
check('within(): form strings from the plugin catalogue', $form === 'Vérification quotidienne', $form);
check('within(): default domain restored', textdomain(null) === 'messages', textdomain(null));

echo "\n$fails failure(s)\n";
exit($fails ? 1 : 0);
