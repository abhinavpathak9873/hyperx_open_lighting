# Known issues

## Modifier keys after initial setup

Ctrl/Shift initially misbehaved during setup. The device owner subsequently
confirmed both were reliable after restarting/reconnecting the keyboard.
The cause of the transient device state was not established; this is not
being tracked as an unresolved streaming defect.

If it happens on another setup, pause lighting and reconnect the keyboard,
then test again. Reports should distinguish physical keystrokes from injected
input. Do not factory-reset or flash firmware as an assumed fix.

## Lighting takeover timeout

Sparse static frames caused the keyboard to alternate between its onboard
lighting and the selected color. Streaming identical frames continuously avoids
the five-second gaps; local timing verification measured 700 acknowledged frames
in 35 seconds, with a 51.9 ms maximum gap. That transport test does not prove
physical LED rendering or modifier correctness. Each device now has its own
worker, so wireless timeout/retry does not stall keyboard frames.

## Wireless limitations

USB receiver mode only. Sleeping devices may not acknowledge commands; retries
are automatic. Continuous effects can shorten battery life. One mouse RGB zone
means rainbow wave is equivalent to color cycling on that device.
