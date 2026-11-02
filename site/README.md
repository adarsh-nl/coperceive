# Landing site

Static site in this directory. The build base is `/coperceive/`.

```bash
npm ci
npm run build
npm run preview
```

## Waitlist

The form posts `{ "email": "<address>" }` as JSON to the URL in
`VITE_WAITLIST_ENDPOINT`. Vite reads that variable at build time. Set it in the
environment that runs `npm run build`. Do not commit a real URL, and do not
name a hosted form service in source.

When `VITE_WAITLIST_ENDPOINT` is unset or blank, the form still renders. Submit
checks the address in the browser, sends no request, stores nothing, and shows
"Waitlist opens soon." A success line is shown only after the configured
endpoint responds with a success status.
