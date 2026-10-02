import unittest
from unittest.mock import Mock, patch, call
from hyperx_open_lighting import lights, app


class LightsTest(unittest.TestCase):
    def test_local_order_waits_for_theme_sync_then_saves_off_again(self):
        events=[]
        states=iter(['active','inactive'])
        def run(*args,**kwargs):
            events.append(args)
            return next(states) if 'show' in args else ''
        with patch.object(lights.Path,'is_file',return_value=True), patch.object(lights,'run',side_effect=run), patch.object(lights.time,'sleep'):
            message=lights.turn_off(lambda:events.append('save'))
        self.assertEqual(events[0],'save')
        self.assertIn('restart',events[1])
        self.assertEqual(events[-2],'save')
        self.assertEqual(events[-1][-3:],('off','--all','--save'))
        self.assertIn('Saved',message)

    def test_failure_is_not_reported_as_success(self):
        save=Mock()
        with patch.object(lights.Path,'is_file',return_value=True), patch.object(lights,'run',side_effect=RuntimeError('restart failed')):
            with self.assertRaisesRegex(RuntimeError,'restart failed'):
                lights.turn_off(save)
        save.assert_called_once()

    def test_portable_fallback_blacks_every_controller_and_closes(self):
        sdk=Mock();sdk.devices.return_value=[(0,'Keyboard',bytes([255])*8),(1,'PC',bytes([255])*4)]
        with patch.object(lights.Path,'is_file',return_value=False), patch.object(lights.openrgb,'SDK',return_value=sdk), patch.object(lights.openrgb,'controller',side_effect=[('Keyboard',bytes(8)),('PC',bytes(4))]):
            lights.turn_off(Mock())
        writes=[c for c in sdk.send.call_args_list if c.args[0]==1050]
        self.assertEqual(len(writes),2)
        self.assertEqual(writes[0].args[1][6:],bytes(8))
        sdk.close.assert_called_once()

    def test_off_preserves_colors_and_enables_paused_devices(self):
        settings={k:{**v,'enabled':False,'color':'#123456'} for k,v in app.DEFAULT.items()}
        with patch.object(app,'load_settings',return_value=settings), patch.object(app,'atomic_json') as save, patch.object(app,'ensure_service'):
            app.save_lights_off()
        result=save.call_args.args[1]
        self.assertTrue(all(x['mode']=='Off' and x['enabled'] and x['color']=='#123456' for x in result.values()))
