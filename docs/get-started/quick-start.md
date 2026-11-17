# Quick start

The first scene is the synthetic demo. It is generated on this machine. It is not a download of OPV2V, Griffin, or any other published dataset.

## In the app

On an empty library, choose **Open the demo**. The same control is on Get Data. The button label is in `web/src/desktop/LibraryScreen.tsx` and `web/src/desktop/GetDataScreen.tsx`.

## On the command line

`coperceive demo create` writes the demo into a new directory (`coperceive demo create --help`):

```bash
coperceive demo create /data/demo
coperceive serve --root /data/demo/scenes
```

`coperceive serve` defaults to host `127.0.0.1` and port `8731`. Open `http://127.0.0.1:8731/`.

`create` also writes a predictions run named `demo` and scores it. That score is a self-check of the generator, not a model result (`docs/datasets/demo-details.md`).

## More detail

- [Demo (synthetic)](../datasets/demo.md)
- [View a scene](../guides/view-a-scene.md)
- [Evaluate predictions](../guides/evaluate.md)
