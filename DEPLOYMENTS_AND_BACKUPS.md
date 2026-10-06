# Deployments and backups

This describes the implementation currently in `surv-backend/` and `surv-project/apps/client/`. A project is the backend record for an application in the console. The system hosts uploaded **static** application builds; it does not build source code or run an application server for each project.

## Ownership and data model

| Concern | Owner | Current representation |
| --- | --- | --- |
| Project identity, organization, deployment status | PostgreSQL `projects` | UUIDv7 `id`, seven-character `public_id`, `organization_id`, `status` (`CREATED` or `DEPLOYED`) |
| Backup history | PostgreSQL `project_backup` | UUIDv7 `id`, `project_id`, relative `archive_path`, `created_at` |
| Current deployment | `PROJECT_UPLOADS_ROOT` on disk | Extracted `public/` directory and `current.zip` |
| Previous deployments | Same disk root | ZIP files under `backups/`, with extracted files for preview |

`project_backup` contains **paths, not ZIP bytes**. Its foreign key cascades on project deletion; the schema does not itself delete files. The default disk root is `./uploads/projects`, relative to the backend process directory. Production needs persistent storage shared by any backend workers or instances that handle the same projects.

For a project with public ID `abc1234`, the directory looks like this after a few deployments:

```text
${PROJECT_UPLOADS_ROOT}/abc1234/
├── current.zip                  # ZIP of the live deployment
├── public/                      # extracted files served as the live site
│   └── index.html
└── backups/
    ├── <32-hex-character-key>.zip
    └── <32-hex-character-key>/
        └── public/              # extracted previous deployment for preview
```

The key is a randomly generated UUID hex value used in the disk path and preview URL. Database `archive_path` stores a relative value such as `backups/<key>.zip`. The extracted backup directory is created during redeployment; if it is missing later, the preview handler extracts the ZIP again on first access.

## Deployment lifecycle

```text
Client folder -> ZIP request -> validate and extract in staging
                                -> move current version to backup (redeploy only)
                                -> publish new public/ and current.zip
                                -> record status and backup row in PostgreSQL
                                -> remove backups older than the newest three
```

1. An Admin or Developer selects or drops a build folder in the project details page. The client requires `index.html` at that folder's root, validates paths and file count, and creates a ZIP containing the folder's **contents**. It estimates the 20 MiB ZIP limit before upload. The ZIP generator emits chunks, then `uploadZipStream` collects them into a `Blob` and sends a raw `application/zip` body with bearer authorization to `PUT /v1/projects/{public_id}/app`.
2. The controller validates the seven-character public ID and content type. `ProjectService` asks the repository for that project with an Admin or Developer membership in its organization. The backend streams the request body to a temporary ZIP on disk and rejects it once more than **20 MiB** has arrived. It does not store the uploaded bytes in PostgreSQL.
3. `ProjectStorage` extracts into a staging directory. It requires root `index.html`, allows at most **10,000 ZIP entries** and **1 GiB of declared extracted file sizes**, and rejects unsafe paths and non-regular file types such as symlinks. Validation finishes before the live directory is replaced.
4. On the first deployment, the staged directory becomes `public/`, the ZIP becomes `current.zip`, and the database status changes from `CREATED` to `DEPLOYED`. No backup row is created.
5. On a replacement deployment, the previous `current.zip` moves to `backups/<key>.zip` and the previous `public/` moves to `backups/<key>/public/`. The new files become current. In one database transaction, the repository inserts the previous archive path into `project_backup`, keeps only the **three newest backups for that project** (ordered by `created_at DESC, id DESC`), and marks the project `DEPLOYED`. The storage layer then deletes the expired ZIPs and extracted directories returned by that transaction.

If publication or the database operation fails before it is recorded, the storage layer attempts to restore the previous `public/` and `current.zip`. Filesystem changes and the PostgreSQL transaction are not one atomic transaction; a process crash or concurrent deployments can still require manual reconciliation. There is currently no deploy lock, backup download API, or restore action. Backups are previews of previous versions, not automatic rollbacks.

## API and serving

| Route | Access | Purpose |
| --- | --- | --- |
| `POST /v1/projects` | Admin or Developer in the organization | Creates a `CREATED` project and its disk directory. |
| `PUT /v1/projects/{public_id}/app` | Admin or Developer in the organization | Replaces the static build; returns `{ "url": "..." }` in the API envelope. |
| `GET /v1/projects/{id}` | Organization member, including QA | Returns project metadata, `status`, and `app_url`. |
| `GET /v1/projects/{id}/backups` | Organization member, including QA | Returns newest-first `{ id, created_at, preview_url }` records. |
| `GET /app/{public_id}/...` | Public | Serves the current extracted build. |
| `GET /app-backups/{public_id}/{key}/...` | Public | Serves a saved build, extracting its ZIP if needed. |

The two site-serving routes do not require a bearer token or consult organization membership. The backup list API is protected, but anyone with a valid preview URL can open that backup while its files remain present. The backup key's format is checked before disk access. The server confines asset paths to the selected `public/` directory, serves an existing file or directory `index.html`, and falls back to root `index.html` for extensionless SPA routes. Missing paths with an extension return 404.

For HTML, JavaScript, and CSS files up to 16 MiB, the server rewrites root-relative references to assets that actually exist in the build under its `/app/{public_id}/` or `/app-backups/{public_id}/{key}/` prefix. HTML also gets the React Router `"basename":"/"` value rewritten to that prefix. Relative references are served unchanged.

In `development`, `dev`, and `staging`, `app_url` is the request origin plus `/app/{public_id}/`, while backup `preview_url` is the same origin plus `/app-backups/{public_id}/{key}/`. In `production`, the live URL is `https://{public_id}.{APPS_DOMAIN}/` and each backup URL is `https://{public_id}-{key}.{APPS_DOMAIN}/`. `APPS_DOMAIN` is required by settings. The backend generates these URLs but does not configure wildcard DNS, TLS, or reverse proxy routing; production infrastructure must map both hostname forms to the corresponding live or backup files.

## Console behavior

The client route `/workspace/:organizationId/projects/:projectId` loads the organization, project, and backup list together. It shows project status and exposes the deployment URL and live iframe only when `status === "DEPLOYED"`. The iframe always points at the current `app_url`; it never switches to a backup. On desktop, CSS renders a 1280 × 800 iframe viewport and scales it to the available panel width. At widths below 1024px, the iframe uses the panel's natural width. The iframe is noninteractive and has scrolling disabled, `sandbox="allow-scripts allow-forms"`, and `referrerPolicy="no-referrer"`. A link covering the preview opens the current deployment in a separate browser tab or window.

The backup section shows up to three previous versions in newest-first order. Each card contains a scaled, noninteractive iframe of its saved deployment and links to the same `preview_url` with `target="_blank"` and `rel="noopener noreferrer"`. Selecting a card opens that backup in a separate browser tab or window; it does not change the main live iframe. Backup thumbnail iframes load lazily and use a sandbox without popup access. After a successful upload, the client refreshes project and backup data.

## Source map

- Schema and retention query: `db/migrations/20261006000001_project_deployments.sql`, `db/queries/projects/projects.sql`.
- HTTP contracts and URL generation: `src/domains/projects/controller.py`, `dto.py`.
- Access checks and deployment decision: `src/domains/projects/service.py`, `repo.py`.
- ZIP validation, disk layout, publication, and static serving: `src/domains/projects/storage.py`.
- Settings and application wiring: `src/core/config.py`, `src/main.py`.
- Client ZIP creation and HTTP transport: `../surv-project/apps/client/app/lib/http/project-zip.ts`, `client.ts`, `workspace.ts`.
- Client project page and preview layout: `../surv-project/apps/client/app/views/project/index.tsx`, `app/app.css`.
- Backend behavior checks: `tests/test_project_service.py`.
