# Deployment Guide — GitHub Actions → GCP Cloud Run

This is the only deployment path. Every push to `main` builds and ships automatically.

---

## What you need before starting

- A Google account with access to [Google Cloud Console](https://console.cloud.google.com)
- A [GitHub](https://github.com) account with this repo pushed to it
- [gcloud CLI](https://cloud.google.com/sdk/docs/install) installed on your machine
- Your `credentials.json` (Google OAuth client secret — see Step 3)
- Your Gemini API key from [aistudio.google.com/apikey](https://aistudio.google.com/apikey)

---

## Step 1 — Create a GCP project

1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Click the project dropdown at the top → **New Project**
3. Give it a name, note the **Project ID** (e.g. `my-workspace-agent`)
4. Make sure billing is enabled: **Billing → Link a billing account**

---

## Step 2 — Enable required APIs

Run this in your terminal (replace `YOUR_PROJECT_ID`):

```bash
gcloud auth login
gcloud config set project YOUR_PROJECT_ID

gcloud services enable \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  secretmanager.googleapis.com \
  iam.googleapis.com \
  iamcredentials.googleapis.com
```

---

## Step 3 — Create Google OAuth credentials

1. In Cloud Console → **APIs & Services → Credentials**
2. Click **Create Credentials → OAuth 2.0 Client ID**
3. Application type: **Web application**
4. Under **Authorised redirect URIs** add:
   ```
   http://localhost:8000/auth/google/callback
   ```
   *(you'll add the real Cloud Run URL later)*
5. Click **Create** → **Download JSON**
6. Save the file as `credentials.json` in your project root *(never commit this)*

---

## Step 4 — Create a service account for GitHub Actions

```bash
export PROJECT_ID=YOUR_PROJECT_ID
export SA=workspace-agent-sa

# Create the service account
gcloud iam service-accounts create $SA \
  --display-name="Workspace Agent CI/CD" \
  --project=$PROJECT_ID

# Grant it the roles it needs
for role in \
  roles/run.admin \
  roles/artifactregistry.admin \
  roles/secretmanager.admin \
  roles/iam.serviceAccountAdmin \
  roles/iam.serviceAccountUser \
  roles/storage.admin; do
  gcloud projects add-iam-policy-binding $PROJECT_ID \
    --member="serviceAccount:${SA}@${PROJECT_ID}.iam.gserviceaccount.com" \
    --role=$role
done
```

---

## Step 5 — Set up Workload Identity Federation

This lets GitHub Actions authenticate to GCP without storing any keys.

```bash
export PROJECT_NUMBER=$(gcloud projects describe $PROJECT_ID \
  --format='value(projectNumber)')
export GITHUB_ORG=YOUR_GITHUB_USERNAME_OR_ORG
export GITHUB_REPO=YOUR_REPO_NAME

# Create the identity pool
gcloud iam workload-identity-pools create github-pool \
  --location=global \
  --display-name="GitHub Actions" \
  --project=$PROJECT_ID

# Create the OIDC provider
gcloud iam workload-identity-pools providers create-oidc github-provider \
  --location=global \
  --workload-identity-pool=github-pool \
  --issuer-uri="https://token.actions.githubusercontent.com" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" \
  --project=$PROJECT_ID

# Allow this repo to impersonate the service account
gcloud iam service-accounts add-iam-policy-binding \
  ${SA}@${PROJECT_ID}.iam.gserviceaccount.com \
  --role=roles/iam.workloadIdentityUser \
  --member="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/github-pool/attribute.repository/${GITHUB_ORG}/${GITHUB_REPO}"

# Print the provider name — you'll need this in Step 6
gcloud iam workload-identity-pools providers describe github-provider \
  --location=global \
  --workload-identity-pool=github-pool \
  --project=$PROJECT_ID \
  --format='value(name)'
```

Copy the output — it looks like:
```
projects/123456789/locations/global/workloadIdentityPools/github-pool/providers/github-provider
```

---

## Step 6 — Add GitHub repository secrets

In your GitHub repo → **Settings → Secrets and variables → Actions → New repository secret**

Add these 5 secrets:

| Secret name | Value |
|---|---|
| `GCP_PROJECT_ID` | Your project ID (e.g. `my-workspace-agent`) |
| `GCP_WORKLOAD_IDENTITY_PROVIDER` | The full provider name from Step 5 |
| `GCP_SERVICE_ACCOUNT` | `workspace-agent-sa@YOUR_PROJECT_ID.iam.gserviceaccount.com` |
| `GEMINI_MODEL` | `gemini-2.5-flash-native-audio-preview-09-2025` |
| `APP_URL` | Leave blank for now — you'll fill this in after first deploy |

---

## Step 7 — Store your secrets in GCP Secret Manager

```bash
# Store your Gemini API key
echo -n "YOUR_GEMINI_API_KEY" | \
  gcloud secrets create workspace-agent-gemini-api-key \
    --data-file=- \
    --replication-policy=automatic \
    --project=$PROJECT_ID

# Store your OAuth credentials.json
gcloud secrets create workspace-agent-google-client-secrets \
  --data-file=credentials.json \
  --replication-policy=automatic \
  --project=$PROJECT_ID
```

---

## Step 8 — Create the Artifact Registry repository

```bash
gcloud artifacts repositories create workspace-agent-repo \
  --repository-format=docker \
  --location=us-central1 \
  --description="Workspace Agent images" \
  --project=$PROJECT_ID
```

---

## Step 9 — Push to main and trigger the first deploy

```bash
git add .
git commit -m "initial deployment"
git push origin main
```

Go to your GitHub repo → **Actions** tab. You'll see the workflow running. It takes about 3–5 minutes on first run.

When it finishes, click the job → scroll to the bottom of the summary to see your **service URL**:
```
https://workspace-agent-XXXXXX-uc.a.run.app
```

---

## Step 10 — Update OAuth redirect URI and APP_URL

**In Google Cloud Console:**
1. APIs & Services → Credentials → click your OAuth client → Edit
2. Under Authorised redirect URIs, add:
   ```
   https://YOUR_SERVICE_URL/auth/google/callback
   ```
3. Also add under Authorised JavaScript origins:
   ```
   https://YOUR_SERVICE_URL
   ```
4. Click **Save**

**In GitHub repo secrets:**
1. Settings → Secrets → update `APP_URL` to `https://YOUR_SERVICE_URL`

**Trigger one more deploy** to apply the URL:
```bash
git commit --allow-empty -m "apply service URL"
git push origin main
```

---

## Step 11 — Verify it's working

```bash
# Health check
curl https://YOUR_SERVICE_URL/health
# Expected: {"status":"ok"}
```

Then open `https://YOUR_SERVICE_URL/ui` in your browser, sign in with Google, click the mic, and speak.

---

## Every deploy after this

Just push to `main`. GitHub Actions handles the rest automatically.

```bash
git add .
git commit -m "your changes"
git push origin main
```

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `Permission denied` in Actions | Check the service account has all roles from Step 4 |
| `redirect_uri_mismatch` OAuth error | The URL in Cloud Console must match exactly — no trailing slash |
| Mic button doesn't work after AI speaks | You must click the mic button yourself — browser autoplay policy prevents auto-start |
| WebSocket disconnects after ~60s | Already fixed via `SessionResumptionConfig` — ensure you're on the latest code |
| 403 on the service URL | Run: `gcloud run services add-iam-policy-binding workspace-agent --region=us-central1 --member=allUsers --role=roles/run.invoker` |
| Actions workflow fails on `auth` step | Confirm the Workload Identity pool/provider names and the `attribute.repository` value match your GitHub org/repo exactly |
