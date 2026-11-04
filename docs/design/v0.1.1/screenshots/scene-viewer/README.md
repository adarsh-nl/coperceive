# Scene viewer: mock vs app

Each image has three panels: the Claude Design mock (`Scene Viewer.dc.html`),
the app with a synthetic scene, and the app with an empty scenes root. The app
runs as the browser build. The scene is made up for these pictures: four
agents at a crossing for 10.6 s, 18 invented vehicles, random lidar points with
a 0.3 s dropout (the sweep-gap markers), four calibrated cameras with plain
gradient images, and a straight test road as the map. It holds no dataset
frames.

- `viewer-<size>-<theme>.jpg`: mock | populated (Bird's-eye at sample 44, a
  box selected) | empty.

Sizes 1600×1000, 1280×800 and 2560×1440, dark and light. The 3D view stays
dark in the light theme, as before. In the browser build the top bar says
"Server · host"; the desktop app shows "Streaming over SSH from <server>" for a
cluster dataset and "Local folder · <name>" for a folder on the Mac. Elements of
the mock that are not built are listed in the PR description.
