# Contributing

Setup and the check commands are in the repository file [CONTRIBUTING.md](https://github.com/adarsh-nl/coperceive/blob/release/v0.1.2/CONTRIBUTING.md). The checkout commands there are:

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[app,griffin,dev]"
bash scripts/dev/install-hooks.sh
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/pyright
.venv/bin/pytest
```

That file says Python 3.11 through 3.13. A new dataset layout is a manifest under `coperceive/adapters/builtin/`, checked against a synthetic tree. See [Add a dataset](../datasets/add-a-dataset.md).

## More detail

- [Design decisions](decisions.md)
- [Code of conduct](https://github.com/adarsh-nl/coperceive/blob/release/v0.1.2/CODE_OF_CONDUCT.md)
