#!/usr/bin/env bash
# =============================================================================
# deploy.sh — Build, push, and deploy Workspace Voice Agent to GCP Cloud Run
#
# Usage:
#   ./deploy/scripts/deploy.sh [options]
#
# Options:
#   -p  PROJECT_ID       GCP project ID (required)
#   -r  REGION           GCP region         (default: us-central1)
#   -n  APP_NAME         Service name       (default: workspace-agent)
#   -t  IMAGE_TAG        Docker image tag   (default: git SHA or 'latest')
#   -m  GEMINI_MODEL     Gemini model name
#   -u  APP_URL          Public URL (used for OAuth redirect — can be set post-deploy)
#   -h                   Show this help
#
# Prerequisites:
#   gcloud CLI authenticated with roles: Editor + Secret Manager Admin + IAM Admin
#   Docker installed and authenticated to Artifact Registry
#   GEMINI_API_KEY and GOOGLE_CLIENT_SECRETS_FILE env vars set
# =============================================================================
set -euo pipefail

# ── Defaults ──────────────────────────────────────────────────────────────────
REGION="us-central1"
APP_NAME="workspace-agent"
IMAGE_TAG="${IMAGE_TAG:-$(git rev-parse --short HEAD 2>/dev/null || echo 'latest')}"
GEMINI_MODEL="gemini-2.5-flash-native-audio-preview-09-2025"
APP_URL=""
PROJECT_ID=""

# ── Colours ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
info()    { echo -e "${BLUE}[INFO]${NC}  $*"; }
success() { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

usage() {
  grep '^#' "$0" | grep -v '#!/' | sed 's/^# \{0,2\}//'
  exit 0
}

# ── Argument parsing ──────────────────────────────────────────────────────────
while getopts "p:r:n:t:m:u:h" opt; do
  case $opt in
    p) PROJECT_ID="$OPTARG" ;;
    r) REGION="$OPTARG" ;;
    n) APP_NAME="$OPTARG" ;;
    t) IMAGE_TAG="$OPTARG" ;;
    m) GEMINI_MODEL="$OPTARG" ;;
    u) APP_URL="$OPTARG" ;;
    h) usage ;;
    *) usage ;;
  esac
done

[[ -z "$PROJECT_ID" ]] && error "PROJECT_ID is required (-p)"

# ── Derived values ────────────────────────────────────────────────────────────
REPO="${REGION}-docker.pkg.dev/${PROJECT_ID}/${APP_NAME}-repo"
IMAGE_URL="${REPO}/${APP_NAME}:${IMAGE_TAG}"

info "=== Workspace Voice Agent — GCP Deployment ==="
info "Project  : $PROJECT_ID"
info "Region   : $REGION"
info "Service  : $APP_NAME"
info "Image    : $IMAGE_URL"
echo ""

# ── Step 1: Verify required env vars ─────────────────────────────────────────
info "Step 1/7 — Checking prerequisites…"
[[ -z "${GEMINI_API_KEY:-}" ]]              && error "GEMINI_API_KEY env var is not set"
[[ -z "${GOOGLE_CLIENT_SECRETS_FILE:-}" ]]  && error "GOOGLE_CLIENT_SECRETS_FILE env var is not set"
[[ ! -f "$GOOGLE_CLIENT_SECRETS_FILE" ]]    && error "GOOGLE_CLIENT_SECRETS_FILE not found: $GOOGLE_CLIENT_SECRETS_FILE"
command -v gcloud >/dev/null 2>&1           || error "gcloud CLI not found"
command -v docker  >/dev/null 2>&1          || error "Docker not found"
success "Prerequisites OK"

# ── Step 2: Set gcloud project ────────────────────────────────────────────────
info "Step 2/7 — Configuring gcloud project…"
gcloud config set project "$PROJECT_ID" --quiet
success "Project set to $PROJECT_ID"

# ── Step 3: Enable required APIs ─────────────────────────────────────────────
info "Step 3/7 — Enabling GCP APIs (this may take a minute on first run)…"
gcloud services enable \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  secretmanager.googleapis.com \
  iam.googleapis.com \
  --quiet
success "APIs enabled"

# ── Step 4: Create Artifact Registry repo if it doesn't exist ────────────────
info "Step 4/7 — Ensuring Artifact Registry repository exists…"
if ! gcloud artifacts repositories describe "${APP_NAME}-repo" \
     --location="$REGION" --quiet >/dev/null 2>&1; then
  gcloud artifacts repositories create "${APP_NAME}-repo" \
    --repository-format=docker \
    --location="$REGION" \
    --description="Docker images for ${APP_NAME}" \
    --quiet
  success "Repository created: ${APP_NAME}-repo"
else
  info "Repository already exists — skipping creation"
fi

# ── Step 5: Build and push Docker image ───────────────────────────────────────
info "Step 5/7 — Building and pushing Docker image…"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

gcloud auth configure-docker "${REGION}-docker.pkg.dev" --quiet

docker build \
  --platform linux/amd64 \
  --tag "$IMAGE_URL" \
  --tag "${REPO}/${APP_NAME}:latest" \
  "$REPO_ROOT"

docker push "$IMAGE_URL"
docker push "${REPO}/${APP_NAME}:latest"
success "Image pushed: $IMAGE_URL"

# ── Step 6: Store secrets in Secret Manager ────────────────────────────────────
info "Step 6/7 — Storing secrets in Secret Manager…"

store_secret() {
  local SECRET_ID="$1"
  local SECRET_VALUE="$2"
  if gcloud secrets describe "$SECRET_ID" --quiet >/dev/null 2>&1; then
    echo -n "$SECRET_VALUE" | gcloud secrets versions add "$SECRET_ID" --data-file=- --quiet
    info "Secret updated: $SECRET_ID"
  else
    echo -n "$SECRET_VALUE" | gcloud secrets create "$SECRET_ID" \
      --data-file=- \
      --replication-policy=automatic \
      --quiet
    success "Secret created: $SECRET_ID"
  fi
}

store_secret "${APP_NAME}-gemini-api-key"          "$GEMINI_API_KEY"
store_secret "${APP_NAME}-google-client-secrets"   "$(cat "$GOOGLE_CLIENT_SECRETS_FILE")"
success "Secrets stored"

# ── Step 7: Deploy to Cloud Run ───────────────────────────────────────────────
info "Step 7/7 — Deploying to Cloud Run…"

REDIRECT_URI="${APP_URL:+${APP_URL}/auth/google/callback}"
REDIRECT_URI="${REDIRECT_URI:-placeholder-update-after-deploy}"

gcloud run deploy "$APP_NAME" \
  --image="$IMAGE_URL" \
  --platform=managed \
  --region="$REGION" \
  --allow-unauthenticated \
  --service-account="${APP_NAME}-sa@${PROJECT_ID}.iam.gserviceaccount.com" \
  --set-env-vars="APP_ENV=production,LOG_LEVEL=INFO,GEMINI_MODEL=${GEMINI_MODEL},GOOGLE_CLOUD_PROJECT=${PROJECT_ID},GOOGLE_CLOUD_LOCATION=${REGION},GOOGLE_OAUTH_REDIRECT_URI=${REDIRECT_URI}" \
  --set-secrets="GEMINI_API_KEY=${APP_NAME}-gemini-api-key:latest,GOOGLE_CLIENT_SECRETS_JSON=${APP_NAME}-google-client-secrets:latest" \
  --memory=512Mi \
  --cpu=1 \
  --min-instances=0 \
  --max-instances=10 \
  --timeout=3600 \
  --quiet

SERVICE_URL=$(gcloud run services describe "$APP_NAME" \
  --platform=managed \
  --region="$REGION" \
  --format="value(status.url)")

echo ""
success "=== Deployment complete! ==="
echo ""
echo -e "  ${GREEN}Service URL:${NC}  $SERVICE_URL"
echo ""

if [[ -z "$APP_URL" ]]; then
  warn "ACTION REQUIRED — Update OAuth settings:"
  echo "  1. Add this to your Google Cloud Console OAuth redirect URIs:"
  echo "     ${SERVICE_URL}/auth/google/callback"
  echo ""
  echo "  2. Re-run this script with -u $SERVICE_URL to apply the URL to the service:"
  echo "     $0 -p $PROJECT_ID -u $SERVICE_URL"
  echo ""
fi
