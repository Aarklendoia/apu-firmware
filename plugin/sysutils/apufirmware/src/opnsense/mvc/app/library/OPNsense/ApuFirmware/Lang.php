<?php

/*
 * Copyright (C) 2026 Édouard Biton
 * All rights reserved.
 *
 * Redistribution and use in source and binary forms, with or without
 * modification, are permitted provided that the following conditions are met:
 *
 * 1. Redistributions of source code must retain the above copyright notice,
 *    this list of conditions and the following disclaimer.
 *
 * 2. Redistributions in binary form must reproduce the above copyright
 *    notice, this list of conditions and the following disclaimer in the
 *    documentation and/or other materials provided with the distribution.
 *
 * THIS SOFTWARE IS PROVIDED ``AS IS'' AND ANY EXPRESS OR IMPLIED WARRANTIES,
 * INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY
 * AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
 * AUTHOR BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY,
 * OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
 * SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
 * INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
 * CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
 * ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
 * POSSIBILITY OF SUCH DAMAGE.
 */

namespace OPNsense\ApuFirmware;

/**
 * Translations of the plugin: its own gettext catalogue (apufirmware.mo, built from
 * plugin/sysutils/apufirmware/lang/*.po), then the OPNsense one, then English.
 * OPNsense only translates the strings of its own plugins.
 */
class Lang
{
    const DOMAIN = 'apufirmware';

    /* status fields that hold a message from apufw.py, with its template in <field>_t */
    const MESSAGES = ['message', 'check_error', 'blocked_reason'];

    private static $bound = false;

    public static function bind()
    {
        if (!self::$bound) {
            bindtextdomain(self::DOMAIN, '/usr/local/share/locale');
            bind_textdomain_codeset(self::DOMAIN, 'UTF-8');
            self::$bound = true;
        }
    }

    public static function _($text)
    {
        if ($text === null || $text === '') {
            return '';
        }
        self::bind();
        $translated = dgettext(self::DOMAIN, $text);
        return $translated !== $text ? $translated : gettext($text);
    }

    /**
     * Run $build (e.g. getForm) with this catalogue as the default gettext domain:
     * OPNsense translates form labels with gettext().
     */
    public static function within(callable $build)
    {
        self::bind();
        $previous = textdomain(null);
        textdomain(self::DOMAIN);
        try {
            return $build();
        } finally {
            textdomain($previous);
        }
    }

    /**
     * Translate the apufw.py messages of a status: each one comes with its English
     * template and arguments (["Download failed: %s", ["timeout"]]). The template is used
     * only when it still gives the message, otherwise the message is kept as is.
     */
    public static function status(array $status)
    {
        foreach (self::MESSAGES as $key) {
            $template = $status[$key . '_t'] ?? null;
            unset($status[$key . '_t']);
            if (!is_string($status[$key] ?? null) || !is_array($template) || count($template) != 2) {
                continue;
            }
            list($text, $args) = $template;
            if (!is_string($text) || !is_array($args)) {
                continue;
            }
            $args = array_values($args);
            try {
                if (@vsprintf($text, $args) !== $status[$key]) {
                    continue;
                }
                $status[$key] = vsprintf(self::_($text), $args);
            } catch (\Throwable $e) {
                /* a translation with other placeholders: keep the English message */
            }
        }
        return $status;
    }
}
