"""One-shot all-lighting off, including the optional local room-light setup."""
from pathlib import Path
import struct
import subprocess
import time
try:
    from . import openrgb
except ImportError:
    import openrgb


def run(*args, timeout=20):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout).strip() or f'{args[0]} failed')
    return result.stdout.strip()


def turn_off(save_hyperx):
    # Persist this first, preventing a running HyperX effect from restoring color.
    save_hyperx()
    helper = Path.home() / '.local/bin/pathak-room-lights'
    if helper.is_file():
        # This installation needs fresh HID handles after keyboard reconnects.
        run('systemctl', '--user', 'restart', 'openrgb-last-horizon.service')
        deadline = time.monotonic() + 45
        while run('systemctl', '--user', 'show', 'pathak-room-lights-sync.service',
                  '-p', 'ActiveState', '--value') in ('activating', 'active'):
            if time.monotonic() >= deadline:
                raise RuntimeError('Theme sync is still running; try Lights off again shortly')
            time.sleep(.5)
        # Startup theme sync also writes settings, so save off again after it ends.
        save_hyperx()
        for attempt in range(10):
            try:
                run(str(helper), 'off', '--all', '--save', timeout=20)
                return 'Off sent to HyperX, PC, keyboard and room lights. Saved.'
            except (RuntimeError, subprocess.TimeoutExpired):
                if attempt == 9:
                    raise
                time.sleep(1)
    else:
        # Portable fallback: all currently detected devices in the local SDK.
        sdk = openrgb.SDK()
        try:
            devices = sdk.devices()
            if not devices:
                raise RuntimeError('No devices detected in OpenRGB')
            for index, name, colors in devices:
                if not colors:
                    continue
                black = bytes(len(colors))
                sdk.send(1100, device=index)
                sdk.send(1050, struct.pack('<IH', 6 + len(black), len(black) // 4) + black, index)
                actual_name, actual = openrgb.controller(sdk.request(1, struct.pack('<I', 2), index))
                if actual_name != name or actual != black:
                    raise RuntimeError(f'OpenRGB did not accept off for {name}')
            return 'Off sent to HyperX and all detected OpenRGB lights.'
        finally:
            sdk.close()
