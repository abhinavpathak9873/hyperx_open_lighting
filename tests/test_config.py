import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from hyperx_open_lighting import app as h


class ConfigTest(unittest.TestCase):
    def test_migration_retains_colors_and_enables_existing_devices(self):
        legacy = {k: {f: v for f, v in value.items() if f != 'enabled'} for k, value in h.DEFAULT.items()}
        legacy['keyboard']['color'] = '#abcdef'
        result = h.validate(legacy)
        self.assertEqual(result['keyboard']['color'], '#abcdef')
        self.assertTrue(all(v['enabled'] for v in result.values()))

    def test_rejects_invalid_settings_before_any_device_io(self):
        for field, value in [('color', '#ffffff00'), ('brightness', True),
                             ('brightness', 101), ('enabled', 'false'), ('mode', 'unknown')]:
            with self.subTest(field=field, value=value):
                settings = {k: dict(v) for k, v in h.DEFAULT.items()}
                settings['keyboard'][field] = value
                with self.assertRaises(ValueError):
                    h.validate(settings)

    def test_disabled_device_never_opens_hardware(self):
        import threading
        shared = {'settings': {k: {**v, 'enabled': False} for k, v in h.DEFAULT.items()}, 'devices': {}}
        stop, guard = threading.Event(), threading.Lock()
        with patch.object(h, 'find_device') as find:
            thread = threading.Thread(target=h.device_worker, args=('keyboard', shared, guard, stop))
            thread.start()
            try:
                stop.wait(.15)
                self.assertEqual(shared['devices']['keyboard']['state'], 'Paused')
                find.assert_not_called()
            finally:
                stop.set()
                thread.join()

    def test_atomic_save_preserves_valid_json(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'settings.json'
            h.atomic_json(path, h.DEFAULT)
            self.assertEqual(h.validate(json.loads(path.read_text())), h.DEFAULT)
            self.assertEqual([p.name for p in path.parent.iterdir()], ['settings.json'])
