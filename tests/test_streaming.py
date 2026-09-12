"""Regression: static/off keep streaming even while mouse I/O times out."""
import threading
import time
import unittest
from unittest.mock import patch
from hyperx_open_lighting import app as h


class SlowMouse:
    def __init__(self, key, path):
        self.key, self.path, self.acks, self.info = key, path, 0, {}

    def brightness(self, value):
        return value

    def exchange(self, data, response=None):
        time.sleep(.6)
        raise TimeoutError('Simulated sleeping wireless mouse')

    def frame(self, colors):
        if self.key == 'mouse':
            time.sleep(.6)
            raise TimeoutError('Simulated sleeping wireless mouse')
        time.sleep(.002)
        self.acks += 7

    def close(self):
        pass


class StreamingTest(unittest.TestCase):
    def test_static_and_off_are_not_blocked_by_mouse(self):
        for mode in ('Static', 'Off'):
            with self.subTest(mode=mode):
                shared = {'settings': {k: {**v, 'mode': mode, 'enabled': True} for k, v in h.DEFAULT.items()},
                          'devices': {}}
                guard, stop = threading.Lock(), threading.Event()
                with patch.object(h, 'Device', SlowMouse), patch.object(h, 'find_device', lambda k: k):
                    threads = [threading.Thread(target=h.device_worker, args=(k, shared, guard, stop))
                               for k in ('keyboard', 'mouse')]
                    for t in threads:
                        t.start()
                    try:
                        time.sleep(1.6)
                        with guard:
                            state = dict(shared['devices'])
                        self.assertGreaterEqual(state['keyboard']['frames_sent'], 25)
                        self.assertLess(state['keyboard']['max_frame_gap_ms'], 150)
                        self.assertEqual(state['mouse']['state'], 'Waiting for mouse to wake or reconnect')
                    finally:
                        stop.set()
                        for t in threads:
                            t.join()


if __name__ == '__main__':
    unittest.main()
