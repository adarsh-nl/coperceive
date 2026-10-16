# SPDX-License-Identifier: Apache-2.0
"""PyInstaller entry. The desktop shell only runs ``coperceive serve``."""

from coperceive.cli import main

if __name__ == "__main__":
    main()
