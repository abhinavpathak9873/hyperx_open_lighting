# Changelog

## 0.1.0

- Native GTK4/libadwaita interface with a custom keyboard-and-mouse icon.
- Independent color, brightness, breathing, cycle, wave and off controls.
- Separate device workers maintain direct-lighting frames at 20 FPS.
- Settings survive app closure; a user service starts at desktop login.
- Reconnect/sleep handling, CLI diagnostics and immediate pause controls.
- Ctrl/Shift operation was confirmed working after reconnecting the keyboard
  during setup. Both devices are enabled by default.
- User installer, Debian package builder, tests and release automation.
