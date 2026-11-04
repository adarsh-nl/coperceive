# App pages: mock vs app

Each image is three panels: the Claude Design mock (`App Pages.dc.html`), the
app with synthetic fixtures (populated), and the app with nothing configured
(empty). The app runs as the browser build with seeded fixtures; no cluster,
dataset frames or real paths are used.

Named `<page>-<width>x<height>-<theme>.jpg`, for page in library, get_data,
local (Open Folder), servers, evaluation (New evaluation) and history (Run
history), at 1600×1000, 1280×800 and 2560×1440, dark and light.

The mock has no light theme: its panel is dark in the light images.

In the browser build there is no free-space reading, so Get Data shows no
disk-fit bar; the desktop app draws it from the real free space.
