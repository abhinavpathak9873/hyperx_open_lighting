import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
from hyperx_open_lighting import app, mouse

# Exact 33/02 readback from a Haste 2 Core Wireless, firmware 4102.
REPORT = bytes.fromhex('33 02 08 0f 01 07 00 ff 00 00 0f 00 00 00 ff 1f 00 ff ff 00 3f 00 00 ff 00 07 02 ff ff ff').ljust(64, b'\0')


class MouseTest(unittest.TestCase):
    def test_decode_and_preserve_non_dpi_fields(self):
        original = mouse.decode(REPORT)
        self.assertEqual(original, {'stages': [400, 800, 1600, 3200], 'active': 1, 'polling_hz': 1000})
        changed = {**original, 'stages': [50, 900, 12000, 3200], 'polling_hz': 500, 'active': 2}
        packet = mouse.build(REPORT, changed)
        self.assertEqual(packet[:4], b'\x32\x01\x00\x00')
        self.assertEqual(len(packet), 64)
        for i in range(4):
            self.assertEqual(packet[9+i*5:12+i*5], REPORT[7+i*5:10+i*5])
        self.assertEqual(packet[27:], REPORT[25:62])
        self.assertEqual(mouse.decode(b'\x33\x02' + packet[4:] + b'\0\0'), changed)

    def test_rejects_bad_dpi_and_ranges(self):
        for value in (0, 49, 51, 12050, 26000, True, 800.0):
            dpi = {**mouse.decode(REPORT), 'stages': [400, value, 1600, 3200]}
            with self.subTest(value=value), self.assertRaises(ValueError):
                mouse.build(REPORT, dpi)
        for index, value in ((3, 31), (4, 4), (2, 2)):
            report = bytearray(REPORT)
            report[index] = value
            with self.assertRaises(ValueError):
                mouse.decode(bytes(report))

    def test_hardware_readback_must_match(self):
        device = Mock()
        device.exchange.side_effect = [[REPORT], [], [REPORT]]
        desired = {**mouse.decode(REPORT), 'active': 2}
        with self.assertRaisesRegex(RuntimeError, 'readback'):
            mouse.apply(device, desired)

    def test_matching_hardware_settings_are_not_rewritten(self):
        device = Mock()
        device.exchange.return_value = [REPORT]
        self.assertEqual(mouse.apply(device, mouse.decode(REPORT)), mouse.decode(REPORT))
        device.exchange.assert_called_once()

    def test_dpi_query_timeout_does_not_interrupt_lighting(self):
        shared = {'settings': {k: dict(v) for k, v in app.DEFAULT.items()}, 'devices': {}}
        stop, guard = threading.Event(), threading.Lock()
        device = Mock(acks=0, info={}, path='mouse')
        with patch.object(app, 'Device', return_value=device) as connect, \
                patch.object(app, 'find_device', return_value='mouse'), \
                patch.object(mouse, 'read', side_effect=TimeoutError('Lost DPI query')) as read:
            worker = threading.Thread(target=app.device_worker, args=('mouse', shared, guard, stop))
            worker.start()
            try:
                time.sleep(.35)
                self.assertEqual(shared['devices']['mouse']['state'], 'Connected')
                self.assertGreaterEqual(device.frame.call_count, 6)
                self.assertEqual(connect.call_count, 1)
                self.assertEqual(read.call_count, 1)  # failed optional query backs off
                self.assertEqual(shared['devices']['mouse']['dpi_query_failures'], 1)
            finally:
                stop.set()
                worker.join()

    def test_rf_retry_does_not_restore_old_saved_stage(self):
        desired = mouse.decode(REPORT)
        current = {**desired, 'active': 2}
        shared = {'settings': {k: dict(v) for k, v in app.DEFAULT.items()}, 'devices': {},
                  'mouse_settings': {'dpi': desired}, 'mouse_revision': 1}
        stop, guard = threading.Event(), threading.Lock()
        first, second = [Mock(acks=0, info={}, path='mouse') for _ in range(2)]
        first.frame.side_effect = [None, None] + [TimeoutError('Lost lighting ACK') for _ in range(3)]
        with patch.object(app, 'Device', side_effect=[first, second]), \
                patch.object(app, 'find_device', return_value='mouse'), \
                patch.object(mouse, 'read', return_value=(REPORT, current)), \
                patch.object(mouse, 'apply', return_value=desired) as apply:
            worker = threading.Thread(target=app.device_worker, args=('mouse', shared, guard, stop))
            worker.start()
            try:
                time.sleep(.75)
                self.assertEqual(apply.call_count, 1)
                self.assertEqual(shared['devices']['mouse']['dpi']['active'], 2)
                self.assertGreater(second.frame.call_count, 3)
            finally:
                stop.set()
                worker.join()

    def test_only_mouse_stage_notifications_are_retained(self):
        d = app.Device.__new__(app.Device)
        d.key, d.dpi_stage = 'mouse', None
        d.observe_stage(b'\xfb\x08\x02')
        self.assertEqual(d.dpi_stage, 2)
        for packet in (b'\xfb\x01\x03', b'\xfb\x08\x04', b'\xfb'):
            d.observe_stage(packet)
            self.assertEqual(d.dpi_stage, 2)
        d.key = 'keyboard'
        d.observe_stage(b'\xfb\x08\x01')
        self.assertEqual(d.dpi_stage, 2)

    def test_pointer_is_per_device_and_validated(self):
        pointer = {'sensitivity': -.25, 'acceleration': 'flat', 'scroll_factor': 1.5, 'natural_scroll': True}
        text = mouse.pointer_text(pointer)
        self.assertIn('hl.device({', text)
        self.assertIn(mouse.DEVICE_NAME, text)
        self.assertNotIn('hl.config', text)
        for value in (float('nan'), float('inf'), True, -2):
            with self.assertRaises(ValueError):
                mouse.pointer_text({**pointer, 'sensitivity': value})

    def test_pointer_reload_failure_restores_both_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'hypr').mkdir()
            main = root / 'hypr/hyprland.lua'
            main.write_text('-- user config\n')
            generated = root / 'hyperx-rgb/pointer.lua'
            generated.parent.mkdir()
            generated.write_text('-- previous\n')
            with patch.object(mouse, 'BASE', root), patch.object(mouse, 'CONFIG', generated.parent / 'mouse.json'), \
                    patch.object(mouse, 'available', return_value=True), \
                    patch.object(mouse, 'run', side_effect=['', '', 'test error', '']):
                with self.assertRaisesRegex(RuntimeError, 'rejected'):
                    mouse.apply_pointer(None)
            self.assertEqual(main.read_text(), '-- user config\n')
            self.assertEqual(generated.read_text(), '-- previous\n')

    def test_paused_lighting_still_applies_dpi_and_same_value_resave(self):
        desired = mouse.decode(REPORT)
        shared = {'settings': {k: {**v, 'enabled': False} for k, v in app.DEFAULT.items()},
                  'devices': {}, 'mouse_settings': {'dpi': desired}, 'mouse_revision': 1}
        guard, stop = threading.Lock(), threading.Event()
        device = Mock(acks=0, info={})
        with patch.object(app, 'Device', return_value=device), patch.object(app, 'find_device', return_value='mouse'), \
                patch.object(mouse, 'apply', return_value=desired) as apply:
            worker = threading.Thread(target=app.device_worker, args=('mouse', shared, guard, stop))
            worker.start()
            try:
                time.sleep(.3)
                with guard:
                    shared['mouse_revision'] = 2
                time.sleep(.3)
                self.assertEqual(apply.call_count, 2)
                device.frame.assert_not_called()
                self.assertEqual(shared['devices']['mouse']['dpi'], desired)
            finally:
                stop.set()
                worker.join()


if __name__ == '__main__':
    unittest.main()
