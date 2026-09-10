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


## Mouse RGB gaps and speed changes in 0.2.0

A timed-out DPI query took the mouse into the same three-second retry path as
a disconnected lighting interface. Reopening the interface also reapplied the
saved DPI stage, potentially undoing a stage selected with the mouse's button.
Version 0.2.1 isolates optional query timeouts, avoids routine background DPI polling, sends RGB
between sensor commands, uses shorter mouse-specific timeouts and wake retries,
and retains the observed stage through transient RF recovery. Matching settings
are read but not rewritten. Keyboard timing is unchanged.

For a fixed sensitivity, use one DPI across all stages and flat desktop
acceleration. A physically sleeping/disconnected mouse cannot accept live RGB;
its onboard effect may appear until it responds again.
