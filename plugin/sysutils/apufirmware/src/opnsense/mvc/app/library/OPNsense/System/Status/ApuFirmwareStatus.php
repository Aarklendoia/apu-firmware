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

namespace OPNsense\System\Status;

use OPNsense\System\AbstractStatus;
use OPNsense\System\SystemStatusCode;

class ApuFirmwareStatus extends AbstractStatus
{
    public function __construct()
    {
        $this->internalPriority = 50;
        $this->internalTitle = gettext('APU coreboot firmware');
        $this->internalLocation = '/ui/apufirmware';
    }

    public function collectStatus()
    {
        $file = '/var/db/apufirmware/status.json';
        if (!is_readable($file)) {
            return;
        }
        $status = json_decode(file_get_contents($file), true);
        if (!is_array($status)) {
            return;
        }
        $this->internalTimestamp = filemtime($file);

        if (!empty($status['needs_coldreset'])) {
            $this->internalStatus = SystemStatusCode::WARNING;
            $this->internalMessage = gettext(
                'New firmware has been written. A cold reset is required to start it.'
            );
        } elseif (($status['state'] ?? '') == 'error') {
            $this->internalStatus = SystemStatusCode::WARNING;
            $this->internalMessage = sprintf(gettext('Firmware update failed: %s'), $status['message'] ?? '');
        } elseif (!empty($status['update_available'])) {
            $this->internalStatus = SystemStatusCode::NOTICE;
            $this->internalMessage = sprintf(
                gettext('Firmware %s is available (installed: %s).'),
                $status['latest_label'] ?? $status['latest_version'] ?? '?',
                $status['bios_version'] ?? '?'
            );
        }
    }
}
