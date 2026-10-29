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

namespace OPNsense\ApuFirmware\Api;

use OPNsense\Base\ApiControllerBase;
use OPNsense\Core\Backend;

class ServiceController extends ApiControllerBase
{
    private function decode($output)
    {
        $result = json_decode(trim($output), true);
        return is_array($result) ? $result : ['state' => 'error', 'message' => trim($output)];
    }

    /* local state, no network */
    public function statusAction()
    {
        return $this->decode((new Backend())->configdRun('apufirmware status'));
    }

    /* query GitHub for the latest build */
    public function checkAction()
    {
        if (!$this->request->isPost()) {
            return ['result' => 'failed'];
        }
        return $this->decode((new Backend())->configdRun('apufirmware check', false, 180));
    }

    /* download, verify, back up and flash, in the background */
    public function updateAction()
    {
        if (!$this->request->isPost()) {
            return ['result' => 'failed'];
        }
        (new Backend())->configdRun('apufirmware flash', true);
        return ['result' => 'started'];
    }

    public function logAction()
    {
        return ['log' => (new Backend())->configdRun('apufirmware log')];
    }

    /* stop services, then power-cycle the board */
    public function coldresetAction()
    {
        if (!$this->request->isPost()) {
            return ['result' => 'failed'];
        }
        (new Backend())->configdRun('apufirmware coldreset', true);
        return ['result' => 'started'];
    }
}
