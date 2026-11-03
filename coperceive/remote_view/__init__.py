# SPDX-License-Identifier: Apache-2.0
"""View prepared cluster scenes from the Mac over SSH, with no Slurm job.

``stream`` runs on the cluster (``coperceive dataset stream``): a read-only file
streamer confined to a dataset's cache root (and its raw root, for camera
originals). ``client`` keeps one SSH connection to it and pipelines requests.
``scenes`` mirrors each scene's small files into a size-capped local cache and
reads point sweeps and images on demand, for the local viewer server.
"""
