{#
 # Copyright (C) 2026 Édouard Biton
 # All rights reserved.
 #
 # Redistribution and use in source and binary forms, with or without
 # modification, are permitted provided that the following conditions are met:
 #
 # 1. Redistributions of source code must retain the above copyright notice,
 #    this list of conditions and the following disclaimer.
 #
 # 2. Redistributions in binary form must reproduce the above copyright
 #    notice, this list of conditions and the following disclaimer in the
 #    documentation and/or other materials provided with the distribution.
 #
 # THIS SOFTWARE IS PROVIDED ``AS IS'' AND ANY EXPRESS OR IMPLIED WARRANTIES,
 # INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY
 # AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
 # AUTHOR BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY,
 # OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
 # SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
 # INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
 # CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
 # ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
 # POSSIBILITY OF SUCH DAMAGE.
 #}

<div class="content-box" style="padding-bottom: 1.5em;">
    <table class="table table-condensed" id="apufw_status">
        <thead>
            <tr><th colspan="2">{{ lang._('Firmware') }}</th></tr>
        </thead>
        <tbody>
            <tr><td style="width:22%">{{ lang._('Board') }}</td><td id="st_board"></td></tr>
            <tr><td>{{ lang._('Installed') }}</td><td id="st_current"></td></tr>
            <tr><td>{{ lang._('Latest build') }}</td><td id="st_latest"></td></tr>
            <tr><td>{{ lang._('Last check') }}</td><td id="st_checked"></td></tr>
            <tr><td>{{ lang._('Status') }}</td><td id="st_message"></td></tr>
        </tbody>
    </table>
    <div class="col-md-12">
        <button class="btn btn-default" id="btn_check" type="button">
            <i class="fa fa-refresh fa-fw"></i> {{ lang._('Check now') }}
        </button>
        <button class="btn btn-primary" id="btn_update" type="button" disabled="disabled">
            <i class="fa fa-download fa-fw"></i> {{ lang._('Update firmware') }}
        </button>
        <button class="btn btn-warning" id="btn_coldreset" type="button" style="display:none">
            <i class="fa fa-power-off fa-fw"></i> {{ lang._('Cold reset') }}
        </button>
        <i id="apufw_spinner" class="fa fa-spinner fa-pulse" style="display:none"></i>
    </div>
    <div class="col-md-12" id="apufw_log_box" style="display:none; margin-top: 1em;">
        <pre id="apufw_log" style="max-height: 400px; overflow-y: auto;"></pre>
    </div>
    <div class="clearfix"></div>
</div>

<div class="content-box">
    {{ partial('layout_partials/base_form', ['fields': generalForm, 'id': 'frm_general', 'apply_btn_id': 'btn_save']) }}
</div>

<script>
$(document).ready(function () {
    let polling = null;
    /* translations arrive HTML-escaped (safe in JS), decode them before .text() */
    const tr = function (s) { return $('<textarea/>').html(s).text(); };

    function setText(id, text) {
        $('#' + id).text(text === undefined || text === null || text === '' ? '-' : text);
    }

    function render(st) {
        setText('st_board', st.product ? st.product : tr('{{ lang._('Not a PC Engines APU') }}'));
        setText('st_current', st.bios_version);

        let latest = $('#st_latest').empty();
        if (st.latest_label) {
            let link = $('<a target="_blank" rel="noopener">').text(st.latest_label);
            if (st.latest_url) { link.attr('href', st.latest_url); }
            latest.append(link);
            if (st.update_available) {
                latest.append(' ').append($('<span class="label label-success">').text(tr('{{ lang._('new') }}')));
            }
        } else {
            latest.text('-');
        }

        setText('st_checked', st.checked_at ? new Date(st.checked_at * 1000).toLocaleString() : '');

        let msg = st.message || '';
        if (st.check_error) { msg = st.check_error; }
        else if (st.state === 'idle' && st.blocked_reason) { msg = st.blocked_reason; }
        else if (st.state === 'idle' && !msg) {
            msg = st.update_available ? tr('{{ lang._('An update is available.') }}') : tr('{{ lang._('Up to date.') }}');
        }
        setText('st_message', msg);
        $('#st_message').toggleClass('text-danger', st.state === 'error' || !!st.check_error);

        let running = st.state === 'running';
        $('#apufw_spinner').toggle(running);
        $('#btn_check').prop('disabled', running);
        $('#btn_update').prop('disabled', running || !st.update_available || !!st.blocked_reason);
        $('#btn_coldreset').toggle(!!st.needs_coldreset && !running);

        if (running || st.state === 'error' || st.state === 'flashed') {
            showLog();
        }
        if (running && polling === null) {
            polling = setInterval(refresh, 2000);
        } else if (!running && polling !== null) {
            clearInterval(polling);
            polling = null;
            showLog();
        }
    }

    function showLog() {
        ajaxGet('/api/apufirmware/service/log', {}, function (data) {
            if (data && data.log) {
                $('#apufw_log_box').show();
                let pre = $('#apufw_log');
                pre.text(data.log);
                pre.scrollTop(pre[0].scrollHeight);
            }
        });
    }

    function refresh() {
        ajaxGet('/api/apufirmware/service/status', {}, render);
    }

    $('#btn_check').click(function () {
        $('#apufw_spinner').show();
        ajaxCall('/api/apufirmware/service/check', {}, function (data) {
            $('#apufw_spinner').hide();
            render(data);
        });
    });

    $('#btn_update').click(function () {
        stdDialogConfirm(
            '{{ lang._('Update firmware') }}',
            '{{ lang._('The current firmware will be backed up, then the new one written and verified. Do not cut the power during the update. A cold reset is needed afterwards to start the new firmware. Continue?') }}',
            '{{ lang._('Update') }}', '{{ lang._('Cancel') }}',
            function () {
                ajaxCall('/api/apufirmware/service/update', {}, function () {
                    $('#apufw_log').text('');
                    setTimeout(refresh, 1000);
                    polling = polling || setInterval(refresh, 2000);
                });
            }, 'warning');
    });

    $('#btn_coldreset').click(function () {
        stdDialogConfirm(
            '{{ lang._('Cold reset') }}',
            '{{ lang._('Services will be stopped and the board powered off for a few seconds, then it will boot the new firmware. The network will be down for a few minutes. Continue?') }}',
            '{{ lang._('Cold reset') }}', '{{ lang._('Cancel') }}',
            function () {
                ajaxCall('/api/apufirmware/service/coldreset', {}, function () {
                    $('#btn_coldreset').prop('disabled', true);
                    setText('st_message', tr('{{ lang._('Restarting. Reload this page in a few minutes.') }}'));
                });
            }, 'warning');
    });

    mapDataToFormUI({'frm_general': '/api/apufirmware/settings/get'}).done(function () {
        $('.selectpicker').selectpicker('refresh');
    });

    $('#btn_save').click(function () {
        saveFormToEndpoint('/api/apufirmware/settings/set', 'frm_general', function () {
            $('#btn_check').click();
        });
    });

    refresh();
});
</script>
