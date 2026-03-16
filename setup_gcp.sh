#!/usr/bin/env bash
# =============================================================================
# setup_gcp.sh — Automates Steps 5–9 of the deployment guide
#
# What it does:
#   Step 5  — Creates the Cloud Run service account + grants IAM roles
#   Step 6  — Creates Workload Identity pool, OIDC provider, repo binding
#   Step 7  — Prints the exact values to paste into GitHub Secrets
#   Step 8  — Stores Gemini API key + OAuth credentials in Secret Manager
#   Step 9  — Creates the Artifact Registry Docker repository
#
# Usage:
#   ./setup_gcp.sh                       # run all steps (5–9)
#   ./setup_gcp.sh --steps 8-9           # run only steps 8 and 9
#   ./setup_gcp.sh --steps 9             # run only step 9
#   ./setup_gcp.sh --steps 5-6          # run only steps 5 and 6
#   ./setup_gcp.sh --fix-oidc            # fix "attribute condition rejected" error
# =============================================================================
set -euo pipefail

# ── Colours ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

info()    { echo -e "${BLUE}[INFO]${NC}  $*"; }
success() { echo -e "${GREEN}[✓]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[✗]${NC}    $*"; exit 1; }
prompt()  { echo -e "${CYAN}[INPUT]${NC} $*"; }
header()  { echo -e "\n${BOLD}${BLUE}══════════════════════════════════════════${NC}"; \
            echo -e "${BOLD} $*${NC}"; \
            echo -e "${BOLD}${BLUE}══════════════════════════════════════════${NC}"; }

# ── Preflight checks ──────────────────────────────────────────────────────────
command -v gcloud >/dev/null 2>&1 \
  || error "gcloud CLI not found. Install it from https://cloud.google.com/sdk/docs/install"
gcloud auth print-access-token >/dev/null 2>&1 \
  || error "Not authenticated. Run: gcloud auth login"

# ── --fix-oidc shortcut ───────────────────────────────────────────────────────
# Handles the "attribute condition rejected" GitHub Actions auth error by
# updating the OIDC provider condition to match the exact repo path.
if [[ "${1:-}" == "--fix-oidc" ]]; then
  clear
  echo ""
  echo -e "${BOLD}${BLUE}  Fix: OIDC Attribute Condition${NC}"
  echo -e "${BLUE}  Resolves: 'The given credential is rejected by the attribute condition'${NC}"
  echo ""
  echo "  This updates your Workload Identity OIDC provider to match"
  echo "  the exact repository path GitHub sends in its token."
  echo ""

  echo -n "  GCP Project ID: "
  read -r FIX_PROJECT
  [[ -z "$FIX_PROJECT" ]] && error "Project ID cannot be empty."

  echo -n "  GitHub username or org (e.g. johndoe): "
  read -r FIX_ORG
  [[ -z "$FIX_ORG" ]] && error "GitHub org cannot be empty."

  echo -n "  GitHub repository name (e.g. workspace-agent): "
  read -r FIX_REPO
  [[ -z "$FIX_REPO" ]] && error "Repository name cannot be empty."

  # GitHub sends the repository claim in lowercase — enforce that here
  FIX_REPO_PATH="${FIX_ORG}/${FIX_REPO}"
  FIX_REPO_PATH_LOWER=$(echo "$FIX_REPO_PATH" | tr '[:upper:]' '[:lower:]')

  echo ""
  echo -e "  ${BOLD}Repo path that will be set:${NC} ${GREEN}${FIX_REPO_PATH_LOWER}${NC}"
  echo -n "  Confirm? Type yes: "
  read -r FIX_CONFIRM
  [[ "$FIX_CONFIRM" != "yes" ]] && { echo "Aborted."; exit 0; }

  echo ""
  info "Updating OIDC provider attribute condition…"
  gcloud iam workload-identity-pools providers update-oidc github-provider \
    --location=global \
    --workload-identity-pool=github-pool \
    --attribute-condition="attribute.repository == '${FIX_REPO_PATH_LOWER}'" \
    --project="$FIX_PROJECT" \
    --quiet

  success "OIDC provider updated."
  echo ""
  echo -e "  ${BOLD}Next:${NC} Re-run your GitHub Actions workflow."
  echo -e "  In your repo → Actions tab → click the failed workflow → Re-run jobs."
  echo ""
  exit 0
fi

# ── Parse --steps flag ────────────────────────────────────────────────────────
STEP_FROM=5
STEP_TO=9

while [[ $# -gt 0 ]]; do
  case "$1" in
    --steps)
      [[ -z "${2:-}" ]] && error "--steps requires a value, e.g. --steps 8-9"
      if [[ "$2" =~ ^([0-9]+)-([0-9]+)$ ]]; then
        STEP_FROM="${BASH_REMATCH[1]}"
        STEP_TO="${BASH_REMATCH[2]}"
      elif [[ "$2" =~ ^([0-9]+)$ ]]; then
        STEP_FROM="${BASH_REMATCH[1]}"
        STEP_TO="${BASH_REMATCH[1]}"
      else
        error "Invalid --steps value '$2'. Use a number (e.g. 8) or range (e.g. 8-9)."
      fi
      shift 2 ;;
    -h|--help)
      grep '^#' "$0" | grep -v '#!/' | sed 's/^# \{0,2\}//'
      exit 0 ;;
    *)
      error "Unknown argument: $1  (use --steps N or --steps N-M)" ;;
  esac
done

# Helper: returns 0 (true) if a given step number is in the requested range
run_step() { [[ "$1" -ge "$STEP_FROM" && "$1" -le "$STEP_TO" ]]; }

# ── Preflight checks ──────────────────────────────────────────────────────────
command -v gcloud >/dev/null 2>&1 \
  || error "gcloud CLI not found. Install it from https://cloud.google.com/sdk/docs/install"
gcloud auth print-access-token >/dev/null 2>&1 \
  || error "Not authenticated. Run: gcloud auth login"

# ── Banner ────────────────────────────────────────────────────────────────────
clear
echo ""
echo -e "${BOLD}${BLUE}  Workspace Agent — GCP Setup Script${NC}"
if [[ $STEP_FROM -eq $STEP_TO ]]; then
  echo -e "${BLUE}  Running: Step $STEP_FROM only${NC}"
else
  echo -e "${BLUE}  Running: Steps $STEP_FROM – $STEP_TO${NC}"
fi
echo ""

# ═════════════════════════════════════════════════════════════════════════════
# GATHER INPUTS — only ask for what the selected steps actually need
# ═════════════════════════════════════════════════════════════════════════════

# Steps 5/6/8/9 all need Project ID and service name — always ask these
header "GCP Project Details"

echo ""
prompt "What is your GCP Project ID?"
echo "  (Find it at console.cloud.google.com — use the ID, not the display name)"
echo -n "  Project ID: "
read -r PROJECT_ID
[[ -z "$PROJECT_ID" ]] && error "Project ID cannot be empty."

info "Verifying project access…"
gcloud projects describe "$PROJECT_ID" --quiet >/dev/null 2>&1 \
  || error "Cannot access project '$PROJECT_ID'. Check the ID and your access."
success "Project '$PROJECT_ID' verified."

echo ""
prompt "Service name — press Enter to use the default."
echo -n "  Service name [workspace-agent]: "
read -r APP_NAME
APP_NAME="${APP_NAME:-workspace-agent}"

echo ""
prompt "GCP region — press Enter to use the default."
echo -n "  Region [us-central1]: "
read -r REGION
REGION="${REGION:-us-central1}"

# Steps 5 & 6 also need GitHub details
if run_step 5 || run_step 6; then
  header "GitHub Repository Details"

  echo ""
  prompt "Your GitHub username or organisation name."
  echo "  (e.g. for github.com/johndoe/my-project  →  johndoe)"
  echo -n "  GitHub username / org: "
  read -r GITHUB_ORG
  [[ -z "$GITHUB_ORG" ]] && error "GitHub username cannot be empty."

  echo ""
  prompt "Your GitHub repository name."
  echo "  (just the repo name, not the full URL — e.g. workspace-agent)"
  echo -n "  Repository name: "
  read -r GITHUB_REPO
  [[ -z "$GITHUB_REPO" ]] && error "Repository name cannot be empty."
else
  # Steps 8/9 don't need GitHub details — set dummies so variable refs don't fail
  GITHUB_ORG=""
  GITHUB_REPO=""
fi

# Step 8 needs the Gemini key and credentials.json path
if run_step 8; then
  header "API Keys & Credentials  (Step 8)"

  echo ""
  prompt "Your Gemini API key."
  echo "  (Get one at aistudio.google.com/apikey — starts with AIza)"
  echo -n "  Gemini API key: "
  read -rs GEMINI_API_KEY
  echo ""
  [[ -z "$GEMINI_API_KEY" ]] && error "Gemini API key cannot be empty."
  success "Gemini API key received."

  echo ""
  prompt "Path to your Google OAuth credentials.json file."
  echo "  (Downloaded from GCP Console → APIs & Services → Credentials)"
  echo -n "  Path to credentials.json [./credentials.json]: "
  read -r CREDS_FILE
  CREDS_FILE="${CREDS_FILE:-./credentials.json}"
  [[ ! -f "$CREDS_FILE" ]] && error "File not found: $CREDS_FILE"
  success "credentials.json found."
else
  GEMINI_API_KEY=""
  CREDS_FILE=""
fi

# ── Confirm ───────────────────────────────────────────────────────────────────
header "Confirm"
echo ""
echo -e "  ${BOLD}Steps to run:${NC}           $STEP_FROM – $STEP_TO"
echo -e "  ${BOLD}GCP Project ID:${NC}         $PROJECT_ID"
echo -e "  ${BOLD}Service name:${NC}           $APP_NAME"
echo -e "  ${BOLD}Region:${NC}                 $REGION"
run_step 5 || run_step 6 && \
  echo -e "  ${BOLD}GitHub repo:${NC}            $GITHUB_ORG/$GITHUB_REPO"
run_step 8 && \
  echo -e "  ${BOLD}Gemini API key:${NC}         ${GEMINI_API_KEY:0:8}…  (hidden)"
run_step 8 && \
  echo -e "  ${BOLD}credentials.json:${NC}       $CREDS_FILE"
echo ""
echo -n "  Looks good? Type yes to continue: "
read -r CONFIRM
[[ "$CONFIRM" != "yes" ]] && { echo "Aborted."; exit 0; }

# ── Derived values ────────────────────────────────────────────────────────────
SA_NAME="${APP_NAME}-sa"
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
POOL_NAME="github-pool"
PROVIDER_NAME="github-provider"
SECRET_GEMINI="${APP_NAME}-gemini-api-key"
SECRET_OAUTH="${APP_NAME}-google-client-secrets"
REGISTRY_REPO="${APP_NAME}-repo"

echo ""
info "Starting — running steps $STEP_FROM to $STEP_TO…"
echo ""

gcloud config set project "$PROJECT_ID" --quiet

# ═════════════════════════════════════════════════════════════════════════════
# STEP 5 — Service account + IAM roles
# ═════════════════════════════════════════════════════════════════════════════
if run_step 5; then
  echo ""
  echo -e "${BOLD}── Step 5: Service Account ────────────────────────────────────${NC}"

  info "Enabling required APIs…"
  gcloud services enable \
    iam.googleapis.com \
    iamcredentials.googleapis.com \
    artifactregistry.googleapis.com \
    secretmanager.googleapis.com \
    run.googleapis.com \
    --quiet
  success "APIs enabled."

  info "Creating service account: $SA_NAME…"
  if gcloud iam service-accounts describe "$SA_EMAIL" --quiet >/dev/null 2>&1; then
    warn "Service account already exists — skipping creation."
  else
    gcloud iam service-accounts create "$SA_NAME" \
      --display-name="$APP_NAME CI/CD" \
      --project="$PROJECT_ID" \
      --quiet
    success "Service account created: $SA_EMAIL"
  fi

  info "Granting IAM roles…"
  ROLES=(
    "roles/run.admin"
    "roles/artifactregistry.admin"
    "roles/secretmanager.admin"
    "roles/iam.serviceAccountAdmin"
    "roles/iam.serviceAccountUser"
    "roles/storage.admin"
  )
  for role in "${ROLES[@]}"; do
    gcloud projects add-iam-policy-binding "$PROJECT_ID" \
      --member="serviceAccount:${SA_EMAIL}" \
      --role="$role" \
      --quiet >/dev/null 2>&1
    success "  $role"
  done
fi

# ═════════════════════════════════════════════════════════════════════════════
# STEP 6 — Workload Identity Federation
# ═════════════════════════════════════════════════════════════════════════════
if run_step 6; then
  echo ""
  echo -e "${BOLD}── Step 6: Workload Identity Federation ───────────────────────${NC}"

  PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')

  info "Creating Workload Identity pool: $POOL_NAME…"
  if gcloud iam workload-identity-pools describe "$POOL_NAME" \
     --location=global --quiet >/dev/null 2>&1; then
    warn "Pool already exists — skipping."
  else
    gcloud iam workload-identity-pools create "$POOL_NAME" \
      --location=global \
      --display-name="GitHub Actions" \
      --project="$PROJECT_ID" \
      --quiet
    success "Pool created: $POOL_NAME"
  fi

  info "Creating OIDC provider: $PROVIDER_NAME…"
  # GitHub always sends the repository claim in lowercase — the condition must match exactly.
  REPO_PATH_LOWER=$(echo "${GITHUB_ORG}/${GITHUB_REPO}" | tr '[:upper:]' '[:lower:]')
  if gcloud iam workload-identity-pools providers describe "$PROVIDER_NAME" \
     --location=global --workload-identity-pool="$POOL_NAME" --quiet >/dev/null 2>&1; then
    warn "Provider already exists — skipping."
  else
    gcloud iam workload-identity-pools providers create-oidc "$PROVIDER_NAME" \
      --location=global \
      --workload-identity-pool="$POOL_NAME" \
      --issuer-uri="https://token.actions.githubusercontent.com" \
      --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" \
      --attribute-condition="attribute.repository == '${REPO_PATH_LOWER}'" \
      --project="$PROJECT_ID" \
      --quiet
    success "OIDC provider created."
  fi

  info "Binding $GITHUB_ORG/$GITHUB_REPO to service account…"
  WI_MEMBER="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL_NAME}/attribute.repository/${GITHUB_ORG}/${GITHUB_REPO}"
  gcloud iam service-accounts add-iam-policy-binding "$SA_EMAIL" \
    --role=roles/iam.workloadIdentityUser \
    --member="$WI_MEMBER" \
    --quiet
  success "Repo binding complete."

  # Print the provider resource name (used in Step 7)
  PROVIDER_RESOURCE=$(gcloud iam workload-identity-pools providers describe "$PROVIDER_NAME" \
    --location=global \
    --workload-identity-pool="$POOL_NAME" \
    --project="$PROJECT_ID" \
    --format='value(name)')
fi

# ═════════════════════════════════════════════════════════════════════════════
# STEP 8 — Secret Manager
# ═════════════════════════════════════════════════════════════════════════════
if run_step 8; then
  echo ""
  echo -e "${BOLD}── Step 8: Secret Manager ─────────────────────────────────────${NC}"

  store_secret() {
    local name="$1"
    local value="$2"
    if gcloud secrets describe "$name" --project="$PROJECT_ID" --quiet >/dev/null 2>&1; then
      echo -n "$value" | gcloud secrets versions add "$name" \
        --data-file=- --project="$PROJECT_ID" --quiet
      warn "Secret '$name' already existed — updated with new version."
    else
      echo -n "$value" | gcloud secrets create "$name" \
        --data-file=- \
        --replication-policy=automatic \
        --project="$PROJECT_ID" \
        --quiet
      success "Secret created: $name"
    fi
  }

  info "Storing Gemini API key…"
  store_secret "$SECRET_GEMINI" "$GEMINI_API_KEY"

  info "Storing OAuth credentials…"
  store_secret "$SECRET_OAUTH" "$(cat "$CREDS_FILE")"
fi

# ═════════════════════════════════════════════════════════════════════════════
# STEP 9 — Artifact Registry
# ═════════════════════════════════════════════════════════════════════════════
if run_step 9; then
  echo ""
  echo -e "${BOLD}── Step 9: Artifact Registry ──────────────────────────────────${NC}"

  info "Creating Docker repository: $REGISTRY_REPO…"
  if gcloud artifacts repositories describe "$REGISTRY_REPO" \
     --location="$REGION" --project="$PROJECT_ID" --quiet >/dev/null 2>&1; then
    warn "Repository already exists — skipping."
  else
    gcloud artifacts repositories create "$REGISTRY_REPO" \
      --repository-format=docker \
      --location="$REGION" \
      --description="Docker images for $APP_NAME" \
      --project="$PROJECT_ID" \
      --quiet
    success "Repository created: $REGISTRY_REPO"
  fi
fi

# ═════════════════════════════════════════════════════════════════════════════
# DONE — Summary
# ═════════════════════════════════════════════════════════════════════════════
echo ""
echo -e "${BOLD}${GREEN}══════════════════════════════════════════════════════════${NC}"
echo -e "${BOLD}${GREEN}  ✅  Done! Steps $STEP_FROM–$STEP_TO complete.${NC}"
echo -e "${BOLD}${GREEN}══════════════════════════════════════════════════════════${NC}"
echo ""

# Show GitHub secrets table only when step 6 was run (that's what generates the values)
if run_step 6; then
  echo -e "${BOLD}  ── GitHub Secrets (Step 7) ─────────────────────────────────────────${NC}"
  echo -e "  Go to: github.com/$GITHUB_ORG/$GITHUB_REPO → Settings → Secrets → Actions"
  echo ""
  echo -e "  ${CYAN}Secret name${NC}                          ${CYAN}Value${NC}"
  echo -e "  ────────────────────────────────────────────────────────────────────"
  echo -e "  ${BOLD}GCP_PROJECT_ID${NC}                       ${GREEN}${PROJECT_ID}${NC}"
  echo -e "  ${BOLD}GCP_WORKLOAD_IDENTITY_PROVIDER${NC}       ${GREEN}${PROVIDER_RESOURCE}${NC}"
  echo -e "  ${BOLD}GCP_SERVICE_ACCOUNT${NC}                  ${GREEN}${SA_EMAIL}${NC}"
  echo -e "  ${BOLD}GEMINI_MODEL${NC}                         ${GREEN}gemini-2.5-flash-native-audio-preview-09-2025${NC}"
  echo -e "  ${BOLD}APP_URL${NC}                              ${YELLOW}(leave blank — fill in after first deploy)${NC}"
  echo -e "  ────────────────────────────────────────────────────────────────────"
  echo ""
fi

echo -e "${BOLD}  ── What was done ────────────────────────────────────────────────────${NC}"
run_step 5 && echo -e "  ${GREEN}✓${NC}  Service account created and IAM roles granted"
run_step 6 && echo -e "  ${GREEN}✓${NC}  Workload Identity pool + OIDC provider configured"
run_step 6 && echo -e "  ${GREEN}✓${NC}  GitHub repo bound to service account (keyless auth)"
run_step 8 && echo -e "  ${GREEN}✓${NC}  Gemini API key stored as: $SECRET_GEMINI"
run_step 8 && echo -e "  ${GREEN}✓${NC}  OAuth credentials stored as: $SECRET_OAUTH"
run_step 9 && echo -e "  ${GREEN}✓${NC}  Artifact Registry repository created: $REGISTRY_REPO"
echo ""

echo -e "${BOLD}  ── Next steps ────────────────────────────────────────────────────────${NC}"
if run_step 6; then
  echo -e "  1. Paste the GitHub secrets above (Step 7)"
  echo -e "  2. ${CYAN}git push origin main${NC}  →  first deploy fires automatically"
  echo -e "  3. Copy the service URL from the Actions job summary"
  echo -e "  4. Add it to your Google OAuth client redirect URIs (Step 11)"
  echo -e "  5. Update APP_URL secret in GitHub and push once more"
elif run_step 9; then
  echo -e "  ➜  ${CYAN}git push origin main${NC}  →  deploy fires automatically"
fi
echo ""
