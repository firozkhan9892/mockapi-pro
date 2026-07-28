# MockAPI - Railway Deployment Guide

## Prerequisites
- GitHub account
- Railway account (https://railway.app)
- Push this repo to GitHub

## Steps

### 1. Push to GitHub
```bash
git init
git add .
git commit -m "Initial commit"
git remote add origin https://github.com/YOUR_USERNAME/mockapi.git
git push -u origin main
```

### 2. Create Railway Project
1. Go to https://railway.app and log in
2. Click **"New Project"**
3. Select **"Deploy from GitHub repo"**
4. Authorize Railway to access your repos
5. Select your `mockapi` repository

### 3. Set Source Directory
1. In the Railway dashboard, go to **Settings**
2. Under **Build**, set **Source Directory** to `backend`
3. This tells Railway to find `requirements.txt` and `Procfile` in the `backend/` folder

### 4. Add Environment Variables
Go to the **Variables** tab and add:

| Variable | Value | Required |
|----------|-------|----------|
| `SECRET_KEY` | Generate a random string (e.g. `python -c "import secrets; print(secrets.token_hex(32))"`) | Yes |
| `GOOGLE_CLIENT_ID` | Your Google OAuth client ID | No |
| `GOOGLE_CLIENT_SECRET` | Your Google OAuth client secret | No |

**Note:** Railway automatically sets the `PORT` variable. Do not set it manually.

### 5. Deploy
1. Railway will automatically build and deploy after you push changes
2. Check the **Deployments** tab for build status
3. Once deployed, click **"Settings"** > **Networking** to generate a public URL

### 6. Generate Public Domain
1. Go to **Settings** > **Networking**
2. Click **"Generate Domain"**
3. Railway gives you a free `*.up.railway.app` URL
4. Your app is now live at that URL

## Important Notes

### SQLite on Railway
Railway uses ephemeral storage. Your SQLite database (`mockapi.db`) will be **lost on every deploy or restart**. This is fine for development/testing. For production, migrate to PostgreSQL (Railway provides it as a managed add-on).

### Free Tier Limits
- $5/month credit (enough for small apps)
- 512 MB RAM, 1 GB disk
- Auto-sleeps after inactivity (first request may take ~30s)

### Useful Commands
```bash
# Check logs in Railway dashboard or CLI
railway logs

# Deploy manually
railway up

# Open the app
railway open
```

## Troubleshooting

**Build fails:** Check that `requirements.txt` is in the `backend/` directory and the source directory is set correctly.

**App crashes on start:** Verify `SECRET_KEY` is set in Railway environment variables.

**Database errors:** SQLite requires write access. Railway provides this by default. If issues persist, check the build logs.
