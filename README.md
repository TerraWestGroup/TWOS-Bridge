# TWOS Bridge

The TerraWest Operating System (TWOS) Bridge — a static control-centre site for TerraWest Group, deployed to Azure Static Web Apps.

## How it works

- `index.html` — the display layer. Fetches `data/twos-state.json` at runtime and renders the Bridge (Operating Board, Owner Command, Wave 2 Shadow, Cash Flow & Budget, Podium, After-Care, Control Room, etc).
- `data/twos-state.json` — the persistent evidence state. This is the file that must be refreshed with new evidence (MechanicDesk, Xero/dataSights, Podium, ANZ Worldline) for the Bridge to show current information. Right now this is a manually-updated snapshot.
- `assets/` — brand assets (logo).
- `.github/workflows/azure-static-web-apps-deploy.yml` — deploys the site to Azure Static Web Apps on every push to `main`, using the `AZURE_STATIC_WEB_APPS_API_TOKEN` repo secret.

## Deployment

This repo deploys directly via the Azure Static Web Apps deployment token (not a linked GitHub integration created through the Azure portal). The token lives in this repo's **Settings → Secrets and variables → Actions** as `AZURE_STATIC_WEB_APPS_API_TOKEN`.

Any push to `main` re-deploys the whole site, including whatever is currently in `data/twos-state.json`.

## What's still needed: nightly automated ingestion

The end goal is a scheduled job that runs every evening, without anyone watching, and:

1. Reads new MechanicDesk `.xls` reports from the Outlook "Mechanic Desk Reports" folder (Bunbury + Busselton).
2. Reads the latest Podium Daily Digest email.
3. Reads new ANZ Worldline settlement PDFs.
4. Queries dataSights for the latest Xero financial evidence.
5. Regenerates `data/twos-state.json` from all of the above.
6. Commits and pushes the updated file to `main`, which triggers the deploy workflow above automatically.
7. If any step fails, raises an Owner-attention flag rather than silently leaving stale data live.

This requires:
- An Azure AD app registration with `Mail.Read` (Application permission) on Microsoft Graph, for unattended Outlook access.
- dataSights credentials suitable for scripted/scheduled queries (or reliance on dataSights's own `trigger_xero_sync` schedule).
- A GitHub Actions workflow (`.github/workflows/nightly-refresh.yml`, not yet created) wired to run on a cron schedule, using the above credentials as repo secrets, and to perform steps 1–6.

None of the credential-dependent pieces are built yet — this repo currently only handles deployment of whatever's manually placed in `data/twos-state.json`.

## Note: logo asset

`assets/terrawest-group-logo.png` is referenced by `index.html` but not yet included in this repo — add the actual TerraWest Group logo file there (same filename) to restore the sidebar/header branding.
