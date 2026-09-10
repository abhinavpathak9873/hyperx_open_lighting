import unittest
from hyperx_open_lighting import app as h


class PacketTest(unittest.TestCase):
    def test_full_keyboard_frame_boundaries_and_last_led(self):
        colors = [(0, 0, 0)] * 103
        colors[-1] = (23, 45, 67)
        packets = h.frame_packets(colors)
        self.assertEqual(len(packets), 7)
        self.assertTrue(all(len(p) == 64 for p in packets))
        self.assertEqual(packets[0][:4], bytes([0x44, 1, 6, 0]))
        self.assertEqual(packets[-1][:4], bytes([0x44, 2, 5, 0]))
        self.assertEqual(packets[-1][10:13], bytes([23, 45, 67]))
        self.assertEqual(packets[-1][13:], bytes(51))

    def test_off_is_black_for_both_models(self):
        for key in h.DEVICES:
            colors = h.render(key, {**h.DEFAULT[key], 'mode': 'Off'}, 42)
            self.assertEqual(colors, [(0, 0, 0)] * h.DEVICES[key]['leds'])

    def test_refuses_oversized_report(self):
        with self.assertRaises(ValueError):
            h.packet(bytes(65))
