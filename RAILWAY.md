# MockAPI - Railway Deployment Guide

This repository is now **Railway-ready out of the box**. A root-level
`Dockerfile` + `railway.json` are committed, so Railway detects the project
automatically — no manual "Source Directory" or builder configuration needed.

## Prerequisites
- GitHub account
- Railway account (https://railway.app)
- Push this repo to GitHub

## Steps

### 1. Push to GitHub
```bash
git init
git add .
git commit -m "Deploy to Railway"
git remote add origin https://github.com/YOUR_USERNAME/mockapi.git
git push -u origin main
```

### 2. Create Railway Project
1. Go to https://railway.app and log in
2. Click **"New Project"**
3. Select **"Deploy from GitHub repo"**
4. Authorize Railway to access your repos
5. Select your `mockapi` repository

Railway will detect the `Dockerfile` at the repo root and build the app.
No source directory or build settings are required.

### 3. Add Environment Variables
Go to the **Variables** tab and add:

| Variable | Value | Required |
|----------|-------|----------|
| `SECRET_KEY` | Generate a random string (e.g. `python -c "import secrets; print(secrets.token_hex(32))"`) | Yes |
| `GOOGLE_CLIENT_ID` | Your Google OAuth client ID | No |
| `GOOGLE_CLIENT_SECRET` | Your Google OAuth client secret | No |
| `DATABASE_PATH` | e.g. `/app/backend/mockapi.db` (or a mounted volume path) | No |

**Note:** Railway automatically sets the `PORT` variable. The app and gunicorn
both respect it (default 5000). Do not set `PORT` manually.

### 4. Deploy
1. Railway automatically builds and deploys after you push changes
2. Check the **Deployments** tab for build status
3. Once deployed, click **"Settings"** > **Networking** > **"Generate Domain"**
4. Your app is live at a free `*.up.railway.app` URL

## How the deployment is configured

- `Dockerfile` (repo root) — Python 3.12 image, installs
  `backend/requirements.txt`, copies `backend/` and `frontend/`, runs gunicorn.
- `railway.json` (repo root) — pins the builder to `DOCKERFILE`, sets the
  gunicorn start command, health check on `/`, and a restart policy.
- `backend/Procfile` — kept for compatibility if you ever switch to the
  Heroku/Railpack builder with root directory `backend`.
- `app.py` — already binds `0.0.0.0` on `int(os.environ.get("PORT", 5000))`
  when run directly; gunicorn binds `0.0.0.0:$PORT` in production.

The app serves the static frontend from `../frontend` relative to the working
directory (`/app/backend` inside the container), so both folders must be
deployed together — the Dockerfile guarantees this.

## Important Notes

### SQLite on Railway
Railway uses ephemeral storage. Your SQLite database (`mockapi.db`) is created
at runtime and will be **lost on every deploy or restart**. This is fine for
development/testing. For persistence, either:
- Mount a volume at the directory containing the DB and set
  `DATABASE_PATH` to it (e.g. `/app/backend/mockapi.db`), or
- Migrate to PostgreSQL (Railway provides it as a managed add-on).

The app auto-generates and persists `SECRET_KEY` to `.secret_key` next to the
database if you don't set it, so restarts do not log users out.

### Free Tier Limits
- $5/month credit (enough for small apps)
- 512 MB RAM, 1 GB disk
- Auto-sleeps after inactivity (first request may take ~30s)

### Useful Commands
```bash
# Check logs
railway logs

# Deploy manually
railway up

# Open the app
railway open
```

## Troubleshooting

**Build fails:** Confirm the `Dockerfile` is at the repo root and the latest
commit is pushed. `railway.json` pins the builder to `DOCKERFILE`, so no
manual build settings are needed.

**App crashes on start:** Verify `SECRET_KEY` is set in Railway variables (or
let the app auto-generate it). Check the deploy logs for a traceback.

**Database errors:** SQLite requires write access. Railway provides a writable
layer by default. For persistence across restarts, use a volume (see above).
