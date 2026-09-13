import unittest

from review_support import run_isolated


class SettingsRoundtripTests(unittest.TestCase):
    def test_exported_extension_settings_survive_import_and_reload(self):
        run_isolated('''
            window = UI.Window()
            hp = dict(B.rules['hp'])
            hp.update(random_waveform=True, random_waveforms=['脉冲'],
                      ramp_enabled=True, ramp_duration_ms=4000, ramp_steps=20)
            zone = {'name':'Camp', 'scene':'Island', 'x':1.0, 'y':2.0,
                    'z':3.0, 'radius':8.0}
            area = dict(B.rules['areaDwell'])
            area.update(enabled=True, area_zones=[zone], area_dwell_seconds=45.0)
            window.rule_editors['hp'].load_rule(hp)
            window.rule_editors['areaDwell'].load_rule(area)
            exported = window.build_settings_export_payload()
            imported = window.validate_imported_rules(exported['rules'])
            for key, fields in [('hp', ['random_waveform','random_waveforms',
                                        'ramp_enabled','ramp_duration_ms','ramp_steps']),
                                ('areaDwell', ['area_zones','area_dwell_seconds'])]:
                for field in fields:
                    assert imported[key][field] == exported['rules'][key][field], (key,field)
                window.rule_editors[key].load_rule(imported[key])
                reloaded = window.rule_editors[key].data()
                for field in fields:
                    assert reloaded[field] == exported['rules'][key][field], (key,field)
            B.rules.update(imported)
            assert UI.save_full_config()[0]
            B.rules['hp']['ramp_enabled'] = False
            B.rules['areaDwell']['area_zones'] = []
            B.load_config()
            assert B.rules['hp']['ramp_enabled'] is True
            assert B.rules['areaDwell']['area_zones'] == [zone]
            assert B.master_output_enabled is False
        ''', ui=True)

    def test_omitted_rules_keep_extension_fields_and_imports_are_normalized(self):
        run_isolated('''
            window = UI.Window()
            B.rules['hp'].update(ramp_enabled=True, ramp_duration_ms=3200,
                                 ramp_steps=16, random_waveform=True,
                                 random_waveforms=['脉冲'])
            result = window.validate_imported_rules({})
            for field in ['ramp_enabled','ramp_duration_ms','ramp_steps',
                          'random_waveform','random_waveforms']:
                assert result['hp'][field] == B.rules['hp'][field], field
            result = window.validate_imported_rules({
                'hp': {'ramp_duration_ms':999999, 'ramp_steps':-3,
                       'random_waveforms':['unknown','脉冲','脉冲']},
                'areaDwell': {'area_dwell_seconds':-1, 'area_zones':[
                    {'x':'invalid'}, {'name':'Camp','radius':-5}]},
                'speedAbove': {'ramp_enabled':True, 'area_zones':[{'x':0}]},
            })
            assert result['hp']['ramp_duration_ms'] == 60000
            assert result['hp']['ramp_steps'] == 2
            assert result['hp']['random_waveforms'] == ['脉冲']
            assert result['areaDwell']['area_dwell_seconds'] == 0.5
            assert len(result['areaDwell']['area_zones']) == 1
            assert result['areaDwell']['area_zones'][0]['radius'] == 0.25
            assert 'ramp_enabled' not in result['speedAbove']
            assert 'area_zones' not in result['speedAbove']
        ''', ui=True)


if __name__ == '__main__':
    unittest.main()
