"""Regression checks for release and privacy failures found in the 0.0.6 audit."""
import unittest

from review_support import run_isolated


class ReleaseHardeningTests(unittest.TestCase):
    def test_malformed_config_and_appearance_do_not_crash_startup(self):
        run_isolated('''
            B.CONFIG_FILE.write_text('[]', encoding='utf-8')
            B.load_config()
            B.CONFIG_FILE.write_text(json.dumps({
                'appearance': {'background_opacity': 'bad',
                               'background_blur': 'NaN', 'accent': 'invalid'}
            }), encoding='utf-8')
            UI.load_full_config()
            assert UI.appearance['background_opacity'] == UI.APPEARANCE_DEFAULTS['background_opacity']
            assert UI.appearance['accent'] == UI.APPEARANCE_DEFAULTS['accent']
        ''', ui=True)

    def test_remote_diagnostics_require_new_explicit_consent(self):
        run_isolated('''
            R = bootstrap.REPORT
            B.CONFIG_FILE.write_text(json.dumps({'reporting': {
                'disable_log_upload': False, 'disable_state_upload': False
            }}), encoding='utf-8')
            R._load_config()
            assert B.network_settings['disable_log_upload'] is True
            assert B.network_settings['disable_state_upload'] is True
            B.CONFIG_FILE.write_text(json.dumps({'reporting': {
                'diagnostics_consent': True,
                'disable_log_upload': False, 'disable_state_upload': True
            }}), encoding='utf-8')
            R._load_config()
            assert B.network_settings['disable_log_upload'] is False
            assert B.network_settings['disable_state_upload'] is True
        ''')

    def test_direct_pairing_and_local_relay_fail_closed(self):
        run_isolated('''
            from unittest.mock import patch
            NET = bootstrap.NET
            B.network_settings['connection_mode'] = NET.MODE_DIRECT
            B.network_settings['direct_host_source'] = NET.HOST_MANUAL
            B.network_settings['manual_host'] = '8.8.8.8'
            assert NET.phone_ws_url('secret') is None
            B.network_settings['manual_host'] = '100.64.1.2'
            assert NET.phone_ws_url('secret').startswith('ws://100.64.1.2:')
            B.network_settings['manual_host'] = 'peer.tailnet.ts.net'
            assert NET.phone_ws_url('secret').startswith('ws://peer.tailnet.ts.net:')
            B.dg_process = None
            with patch.object(B, '_server_port_open', return_value=True):
                assert B.server_running() is False
                assert B.start_server() is False
        ''')

    def test_failed_network_save_keeps_applied_route(self):
        run_isolated('''
            from unittest.mock import patch
            NET = bootstrap.NET
            window = UI.Window()
            previous = dict(B.network_settings)
            window._set_tab_for_mode(NET.MODE_OFFICIAL)
            window.msg_error = lambda *args: None
            with patch.object(NET, '_persist_network_only', return_value=(False, 'read only')):
                window.network_apply()
            assert B.network_settings == previous
        ''', ui=True)

    def test_window_shutdown_releases_relay_before_update(self):
        run_isolated('''
            import subprocess
            process = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
            try:
                B.dg_process = process
                B.dg_log = (root / 'relay.log').open('a', encoding='utf-8')
                window = UI.Window()
                assert window.close()
                assert process.poll() is not None
                assert B.dg_log.closed
                assert B.stop_event.is_set()
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
        ''', ui=True)

    def test_failed_atomic_ui_save_preserves_previous_config(self):
        run_isolated('''
            from unittest.mock import patch
            B.CONFIG_FILE.write_text('{"sentinel": true}', encoding='utf-8')
            with patch.object(UI.os, 'replace', side_effect=OSError('read only')):
                ok, message = UI.save_full_config()
            assert not ok and 'read only' in message
            assert B.CONFIG_FILE.read_text(encoding='utf-8') == '{"sentinel": true}'
        ''', ui=True)

    def test_update_rejects_source_and_incomplete_archives(self):
        run_isolated('''
            import io
            import zipfile
            from unittest.mock import patch
            UPDATE = bootstrap.UPDATE
            good_url = 'https://github.com/example/release/download/Coyote_Windows_x64_Portable.zip'
            release = {'assets': [
                {'name': 'coyote-source.zip', 'browser_download_url': good_url, 'size': 900},
                {'name': 'Coyote_Windows_x64_Portable.zip', 'browser_download_url': good_url},
            ]}
            assert UPDATE._select_update_asset(release)['name'] == 'Coyote_Windows_x64_Portable.zip'
            assert UPDATE._select_update_asset({'assets': release['assets'][:1]}) is None
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, 'w') as archive:
                archive.writestr('README.md', 'source only')
            payload = buffer.getvalue()
            class Response(io.BytesIO):
                headers = {}
                def __enter__(self): return self
                def __exit__(self, *args): self.close()
            asset = dict(release['assets'][1], size=len(payload))
            with patch.object(UPDATE, '_install_root', return_value=root), \\
                 patch.object(UPDATE.urllib.request, 'urlopen', side_effect=lambda *a, **kw: Response(payload)):
                try:
                    UPDATE._download_and_stage({'assets': [asset]})
                except RuntimeError as exc:
                    assert '缺少必要文件' in str(exc), exc
                else:
                    raise AssertionError('Incomplete portable package was accepted')
        ''')


if __name__ == '__main__':
    unittest.main()
