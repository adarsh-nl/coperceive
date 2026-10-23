# M2c: Import from HPC

Plan only. Build this after M2b (`Import from folder…`) is in.

M2a (`coperceive scene pack`) and M2b already pack a scene on the machine where the files are. M2c runs that same pack on the cluster, then copies the finished scene to the Mac library. It does not grow a second copy implementation.

## What the Mac does

1. The Connect screen gains **Import from HPC…** next to **Import from folder…**. The user picks a saved SSH host (the host alias already used by `src-tauri/src/ssh.rs`), the remote scene directory, the image mode (`full`, `preview`, or `none`), and the local library folder.
2. The app opens the existing ControlMaster session (`BatchMode=yes`, `ControlPath=~/.coperceive/ssh-%C`, `ControlPersist=10m`). No new SSH stack.
3. Over that session it runs the cluster's `python -m coperceive scene pack` with the same flags M2b uses, including `--media-root` and `--griffin-root` when the scene's images live outside the scene directory. Stdout is the same NDJSON progress the folder import already shows.
4. Pack writes `<remote-dest>/<scene>.partial/` and renames it only after `COMPLETE.json`. The remote dest is a directory the user chooses under their home, not `/local` (node-local scratch disappears with the job) and not the Griffin release tree.
5. When the remote directory no longer ends in `.partial`, the app runs `rsync -a --partial --append-verify` over the same SSH master to the Mac library. `--partial` and `--append-verify` are what make a dropped eduVPN session resumable. Rsync's destination is a `<scene>.partial` folder on the Mac; the app renames it to `<scene>` only after rsync exits 0.
6. Cancel sends SIGTERM to the remote pack, or to the local rsync. The next Import from HPC on the same paths resumes: pack skips files whose size and sha256 match, and rsync continues from the partial transfer.
7. The viewer and validator already ignore a directory whose name ends in `.partial`, so a half-copied scene never appears in the library list.

## What it does not do

- It does not compile or run on the login node. The Mac invokes SSH; the cluster runs the pack.
- It does not read the Griffin release over NFS from the Mac. The bytes that move are the packed scene.
- It does not start until M2b's progress, cancel, and resume path has been used for real.
