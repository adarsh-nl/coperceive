# SPDX-License-Identifier: Apache-2.0
"""A stand-in for the user's OpenCOOD checkout, for CI only.

The plugin's ``engine`` imports these modules from the OpenCOOD environment at runtime. This
package mirrors the small surface it drives — ``yaml_utils.load_yaml``,
``train_utils.create_model`` and a dataset class with the same pre/post-processing protocol —
so the real runner chain (``coperceive model run`` → ``eval run``) is exercised without a GPU
or the real OpenCOOD. It is not part of any plugin and is never installed.
"""

__version__ = "0.0.0-fake"
