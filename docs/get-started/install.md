# Install

Install a released build, or install the library from a checkout. Dataset files are not in the repository.

## Released builds

The release README lists these files from [GitHub Releases](https://github.com/adarsh-nl/coperceive/releases/latest):

| Platform | File |
| --- | --- |
| macOS Apple Silicon | `Coperceive_0.1.1_aarch64.dmg` |
| macOS Intel | `Coperceive_0.1.1_x64.dmg` |
| Windows x86_64 | `Coperceive_0.1.1_x64-setup.exe` |
| Linux x86_64 | `Coperceive_0.1.1_amd64.AppImage` and `Coperceive_0.1.1_amd64.deb` |

The same README records three limits:

- The Intel Mac image is verified under Rosetta 2 in CI, and is not yet tested on physical Intel Mac hardware.
- Linux in-app updates apply to the `.AppImage`. A `.deb` install is updated with the package manager or by installing a newer `.deb`.
- Unsigned Windows builds show SmartScreen on first run. The README says to use **More info**, then **Run anyway**.

## Library checkout

The release README requires Python 3.11 to 3.13, Node.js 20 or newer, and Rust 1.80 or newer to build the desktop application from source. The Python library itself is:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[app,dev]"
```

## This documentation site

```bash
pip install -r requirements-docs.txt
mkdocs build --strict
```

`requirements-docs.txt` pins MkDocs 1.6.1 and Material 9.7.7, the pair named in [ADR 0005](../adr/0005-docs-generator.md). The `docs` extra in `pyproject.toml` uses the same pins.

## More detail

- [Updates](../desktop/updates.md)
- [v0.1.1 notes in the repository](https://github.com/adarsh-nl/coperceive/blob/release/v0.1.2/docs/release/v0.1.1-notes.md)
