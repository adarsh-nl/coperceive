# Viewer fixes before v0.1.0: before and after

The browser build in desktop mode, with the desktop controller faked so the top
bar reads like a cluster dataset ("Streaming over SSH from Example University
HPC cluster", job 4242). The scene is made up for these pictures (no dataset
frames): `demo_crossing_00031`, a 19-character id like a real V2XSet scene
name, the 31st of 33 scenes, four agents at a crossing, and eight calibrated
cameras on two vehicles with plain gradient images.

- `overlap-<size>-<theme>.jpg`: before (`feat/m2-import` 5e9704c) and after, with
  the camera viewer closed and open, at 1280×800, 1440×900, 1600×1000 and
  2560×1440, dark and light. Before: the connection text sits on top of "31 of 33"
  and the ‹ › buttons; with the camera viewer open the mode switch covers the
  "Orbit · t =" chip and the frame chip. After: one top-bar row where the
  connection text truncates, and a chip row where the switch moves to its own row
  and the chips shorten.
- `camera-strip-<theme>.jpg`: Settings → Viewer → "Camera strip": All cameras
  (scroll), the default, and One agent at a time.
- `update-card-<theme>.jpg`: the update card above Settings, and What's new open.
- `update-dot.jpg`: the collapsed sidebar's dot on its toggle.

The left panel's clipped rows ("a frustums") came from the panel being scrolled
sideways in the desktop app's WebKit. Chromium did not reproduce it, so there is
no before picture. `tests/test_viewer_layout.py` asserts that the panel cannot
scroll sideways and that no text starts left of it.
