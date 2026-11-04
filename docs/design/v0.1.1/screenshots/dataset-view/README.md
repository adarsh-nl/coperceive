# Dataset view v2: mock vs app

Each image has three panels. The first is always the Claude Design mock
(`HPC Dataset View v2.dc.html`); the app runs as the browser build with
synthetic fixtures (made-up 2024 recording times, invented counts).

- `flat-<size>-<theme>.jpg`: mock, the app with no grouping (the default, a
  list sorted by scene name), the app with an empty split.
- `grouped-<size>-<theme>.jpg`: mock, the app grouped by recording day
  ("Recorded Tue 5 Mar 2024"; scenes whose names hold no date come last), the
  app grouped by state.

Sizes 1600×1000, 1280×800 and 2560×1440, dark and light. The mock has no
light theme: its panel is dark in the light images. The mock's top tab bar,
per-day timeline strips, per-agent sensor rows and "Polling … updated 2 s
ago" are not built; see the PR description.
