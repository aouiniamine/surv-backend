# Surv example apps

Each folder is a ready-to-upload static application. Create a Surv project for each example, then select the **folder itself** in the console's **Upload build** dialog. No package installation or build step is required.

| Folder | What it exercises | Quick check |
| --- | --- | --- |
| `pulseboard/` | Relative CSS and JavaScript assets, responsive dashboard UI | Switch the 7/30/90 day controls and see the metrics change. |
| `meridian-market/` | Root-relative `/assets/...` references in HTML and JavaScript | Filter products, search, and add an item to the bag. |
| `wayfinder-journal/` | Client-side navigation and SPA fallback on deep links | Open an entry, then refresh its `/entry/...` URL. |

To test deployment backups, upload any example, change a visible heading in its `index.html`, and upload the folder again. The previous version should appear on the project's backup gallery. Repeat to confirm that only the three most recent backups remain.

To upload with the API, ZIP the **contents** of a folder so `index.html` is at the archive root. For example:

```sh
cd examples/pulseboard
python3 -m zipfile -c /tmp/pulseboard.zip index.html assets
```

Then send `/tmp/pulseboard.zip` as an `application/zip` request body to `PUT /v1/projects/<public_id>/app` with your bearer token. Every example is well below the 20 MiB upload limit.
