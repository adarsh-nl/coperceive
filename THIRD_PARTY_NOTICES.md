# Third-party notices

This repository does not redistribute Griffin recordings. Download them from the publishers.

## Griffin toolkit

The reader was written from the public dataset layout and from files in the release. It does not copy the toolkit’s source. The toolkit’s licence, fetched from `LICENSE.txt` on 2026-10-15, is:

```
MIT License

Copyright (c) [2025] [Jiahao Wang]

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

The Hugging Face dataset card YAML says `license: mit`. That covers the software the card is attached to. It is not permission to commit the recordings here.

## CARLA

Town OpenDRIVE files are read from the CARLA 0.9.15 package, `https://downloads.carlasim.com/Linux/CARLA_0.9.15.tar.gz`. CARLA is MIT, `https://github.com/carla-simulator/carla`. The `.xodr` bytes and the derived map directories are not committed. `coperceive maps fetch` writes them outside this repository.

## Python and JavaScript dependencies

NumPy, PyArrow, Pillow, FastAPI, Uvicorn, Pydantic, huggingface_hub, and the development tools are installed from PyPI under their own licences. They are not vendored. MP4 export runs a system `ffmpeg` when it is on `PATH`. That binary is not vendored.

The browser build bundles the direct `web/` dependencies. Their `package.json` licence fields at install time:

| Package | Licence field |
| --- | --- |
| react, react-dom | MIT |
| @tanstack/react-query | MIT |
| @radix-ui/react-label | MIT |
| openapi-fetch | MIT |
| three | MIT |
| @fontsource/ibm-plex-sans, @fontsource/ibm-plex-mono | OFL-1.1 |
| @fontsource-variable/geist, @fontsource-variable/geist-mono | OFL-1.1 |

Transitive licences ship inside the built assets. `web/node_modules` is not committed.

## Geist and Geist Mono

The page imports Geist and Geist Mono. Both are SIL Open Font License 1.1.

Copyright 2024 The Geist Project Authors (<https://github.com/vercel/geist-font>)

The licence text is part of the package, next to the woff2 files in the built page:

- `coperceive/LICENSES/Geist-OFL.txt`
- `coperceive/LICENSES/GeistMono-OFL.txt`

IBM Plex Sans and IBM Plex Mono remain dependencies of `web/` and are not the faces the page imports.
