"""QuadCast 2 S control through the local OpenRGB SDK (protocol 2)."""
import socket
import struct
import time

NAME = 'HyperX QuadCast 2 S'
LEDS = 108


def color_payload(colors):
    if len(colors) != LEDS or any(len(c) != 3 or any(type(v) is not int or not 0 <= v <= 255 for v in c) for c in colors):
        raise ValueError('QuadCast 2 S requires 108 RGB colors')
    return struct.pack('<IH', 6 + LEDS * 4, LEDS) + b''.join(bytes(c) + b'\0' for c in colors)


def worker(shared, guard, stopping, render):
    """OpenRGB owns USB/keepalive; follow mode never sends color or mode writes."""
    sdk, applied, index = None, None, None
    frames = 0
    try:
        while not stopping.is_set():
            started = time.monotonic()
            delay = 1.
            with guard:
                settings = shared['settings']['microphone'].copy()
            try:
                if not settings['enabled']:
                    if sdk:
                        sdk.close()
                    sdk, applied, index = None, None, None
                    state = {'state': 'App control paused · OpenRGB manages microphone'}
                else:
                    if sdk is None:
                        sdk = SDK()
                    if index is None:
                        matches = [(i, c) for i, name, c in sdk.devices() if name == NAME and len(c) == LEDS * 4]
                        if len(matches) != 1:
                            raise RuntimeError('Waiting for QuadCast 2 S in OpenRGB · rescan after connecting')
                        index = matches[0][0]
                    # Device indices can change on rescan. Verify before every write.
                    name, actual = controller(sdk.request(1, struct.pack('<I', 2), index))
                    if name != NAME or len(actual) != LEDS * 4:
                        raise RuntimeError('OpenRGB devices changed · reconnecting')
                    follow = settings['mode'] == 'Follow OpenRGB'
                    animated = settings['mode'] in ('Breathing', 'Color cycle', 'Rainbow wave')
                    if not follow and (settings != applied or animated):
                        colors = render('microphone', settings, started)
                        colors = [tuple(v * settings['brightness'] // 100 for v in color) for color in colors]
                        payload = color_payload(colors)
                        if settings != applied:
                            sdk.send(1100, device=index)
                        sdk.send(1050, payload, index)
                        name, actual = controller(sdk.request(1, struct.pack('<I', 2), index))
                        if name != NAME or actual != payload[6:]:
                            raise RuntimeError('OpenRGB color readback did not match')
                        frames += 1
                    applied = settings
                    delay = .05 if animated else 1.
                    state = {'state': 'Connected', 'mode': settings['mode'],
                             'brightness': settings['brightness'], 'leds': LEDS,
                             'backend': 'OpenRGB', 'frames_sent': frames,
                             'color': '#' + actual[:3].hex(),
                             'openrgb_device': index}
            except (OSError, RuntimeError, ValueError, struct.error) as exc:
                if sdk:
                    sdk.close()
                sdk, applied, index = None, None, None
                state = {'state': 'OpenRGB: ' + str(exc), 'backend': 'OpenRGB'}
                delay = 2.
            with guard:
                shared['devices']['microphone'] = state
            stopping.wait(max(0, delay - (time.monotonic() - started)))
    finally:
        if sdk:
            sdk.close()


class SDK:
    def __init__(self):
        self.sock = socket.create_connection(('127.0.0.1', 6742), timeout=2)
        try:
            version = struct.unpack('<I', self.request(40, struct.pack('<I', 2)))[0]
            if version < 2:
                raise RuntimeError('OpenRGB SDK protocol 2 or later is required')
            self.send(50, b'HyperX Open Lighting\0')
        except BaseException:
            self.sock.close()
            raise

    def read(self, size):
        result = b''
        while len(result) < size:
            chunk = self.sock.recv(size - len(result))
            if not chunk:
                raise RuntimeError('OpenRGB disconnected')
            result += chunk
        return result

    def send(self, command, data=b'', device=0):
        self.sock.sendall(struct.pack('<4sIII', b'ORGB', device, command, len(data)) + data)

    def request(self, command, data=b'', device=0):
        self.send(command, data, device)
        deadline = time.monotonic() + 2
        while True:
            if time.monotonic() > deadline:
                raise TimeoutError('OpenRGB response timed out')
            magic, target, received, size = struct.unpack('<4sIII', self.read(16))
            if magic != b'ORGB' or size > 16 * 1024 * 1024:
                raise RuntimeError('Invalid OpenRGB packet')
            payload = self.read(size)
            if received == command and target == device:
                return payload

    def close(self):
        self.sock.close()

    def devices(self):
        count = struct.unpack('<I', self.request(0))[0]
        if count > 1024:
            raise ValueError('Invalid OpenRGB device count')
        return [(i, *controller(self.request(1, struct.pack('<I', 2), i))) for i in range(count)]


def controller(data):
    """Read protocol-2 name and colors, including variable mode/zone blocks."""
    offset = 0

    def take(n):
        nonlocal offset
        if n < 0 or offset + n > len(data):
            raise ValueError('Truncated OpenRGB controller')
        result = data[offset:offset+n]
        offset += n
        return result

    def number(fmt):
        return struct.unpack('<' + fmt, take(struct.calcsize('<' + fmt)))[0]

    def string():
        return take(number('H')).rstrip(b'\0').decode('utf-8', errors='replace')

    if number('I') != len(data):
        raise ValueError('Invalid controller size')
    take(4)
    name = string()
    for _ in range(5):
        string()
    modes = number('H')
    take(4)
    for _ in range(modes):
        string()
        take(36)
        take(number('H') * 4)
    for _ in range(number('H')):
        string()
        take(16)
        take(number('H'))
    for _ in range(number('H')):
        string()
        take(4)
    colors = take(number('H') * 4)
    if offset != len(data):
        raise ValueError('Unexpected OpenRGB controller data')
    return name, colors
