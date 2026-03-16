################################################################################
# Workspace Voice AI Assistant — GCP Cloud Run Deployment
# terraform >= 1.6  |  google provider >= 5.0
################################################################################

terraform {
  required_version = ">= 1.6"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }

  # Uncomment and configure to store state in GCS (recommended for teams):
  # backend "gcs" {
  #   bucket = "YOUR_TERRAFORM_STATE_BUCKET"
  #   prefix = "workspace-agent/state"
  # }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

################################################################################
# Enable required APIs
################################################################################

resource "google_project_service" "run" {
  service            = "run.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "artifact_registry" {
  service            = "artifactregistry.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "secret_manager" {
  service            = "secretmanager.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "iam" {
  service            = "iam.googleapis.com"
  disable_on_destroy = false
}

################################################################################
# Artifact Registry — Docker repository for container images
################################################################################

resource "google_artifact_registry_repository" "app" {
  location      = var.region
  repository_id = "${var.app_name}-repo"
  description   = "Docker images for ${var.app_name}"
  format        = "DOCKER"

  depends_on = [google_project_service.artifact_registry]
}

locals {
  image_url = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.app.repository_id}/${var.app_name}:${var.image_tag}"
}

################################################################################
# Service account for Cloud Run
################################################################################

resource "google_service_account" "run_sa" {
  account_id   = "${var.app_name}-sa"
  display_name = "Service account for ${var.app_name} Cloud Run service"
  depends_on   = [google_project_service.iam]
}

# Allow the service account to access secrets
resource "google_project_iam_member" "secret_accessor" {
  project = var.project_id
  role    = "roles/secretmanager.secretAccessor"
  member  = "serviceAccount:${google_service_account.run_sa.email}"
}

################################################################################
# Secret Manager — store sensitive config out of the container image
################################################################################

resource "google_secret_manager_secret" "gemini_api_key" {
  secret_id = "${var.app_name}-gemini-api-key"
  replication { auto {} }
  depends_on = [google_project_service.secret_manager]
}

resource "google_secret_manager_secret_version" "gemini_api_key" {
  secret      = google_secret_manager_secret.gemini_api_key.id
  secret_data = var.gemini_api_key
}

resource "google_secret_manager_secret" "google_client_secrets" {
  secret_id = "${var.app_name}-google-client-secrets"
  replication { auto {} }
  depends_on = [google_project_service.secret_manager]
}

resource "google_secret_manager_secret_version" "google_client_secrets" {
  secret      = google_secret_manager_secret.google_client_secrets.id
  secret_data = var.google_client_secrets_json
}

################################################################################
# Cloud Run service
################################################################################

resource "google_cloud_run_v2_service" "app" {
  name     = var.app_name
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = google_service_account.run_sa.email

    scaling {
      min_instance_count = var.min_instances
      max_instance_count = var.max_instances
    }

    containers {
      image = local.image_url

      # ── Runtime environment ──────────────────────────────────────────
      env {
        name  = "APP_ENV"
        value = "production"
      }
      env {
        name  = "LOG_LEVEL"
        value = var.log_level
      }
      env {
        name  = "GEMINI_MODEL"
        value = var.gemini_model
      }
      env {
        name  = "GOOGLE_OAUTH_REDIRECT_URI"
        value = "${var.app_url}/auth/google/callback"
      }
      env {
        name  = "CORS_ALLOW_ORIGINS"
        value = var.app_url
      }
      env {
        name  = "GOOGLE_CLOUD_PROJECT"
        value = var.project_id
      }
      env {
        name  = "GOOGLE_CLOUD_LOCATION"
        value = var.region
      }

      # ── Secrets (mounted as env vars from Secret Manager) ────────────
      env {
        name = "GEMINI_API_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.gemini_api_key.secret_id
            version = "latest"
          }
        }
      }
      env {
        name = "GOOGLE_CLIENT_SECRETS_JSON"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.google_client_secrets.secret_id
            version = "latest"
          }
        }
      }

      # ── Resources ─────────────────────────────────────────────────────
      resources {
        limits = {
          cpu    = var.cpu
          memory = var.memory
        }
        cpu_idle          = true   # scale CPU to 0 when idle
        startup_cpu_boost = true   # extra CPU during cold start
      }

      # ── Health check ──────────────────────────────────────────────────
      liveness_probe {
        http_get {
          path = "/health"
          port = 8080
        }
        initial_delay_seconds = 10
        period_seconds        = 30
        failure_threshold     = 3
      }

      startup_probe {
        http_get {
          path = "/health"
          port = 8080
        }
        initial_delay_seconds = 5
        period_seconds        = 5
        failure_threshold     = 10
      }
    }

    # Cloud Run's default request timeout is 300s.
    # Raise to 3600s so long-running voice sessions aren't killed mid-call.
    timeout = "3600s"
  }

  depends_on = [
    google_project_service.run,
    google_artifact_registry_repository.app,
    google_secret_manager_secret_version.gemini_api_key,
    google_secret_manager_secret_version.google_client_secrets,
  ]
}

################################################################################
# IAM — make the service publicly accessible (unauthenticated)
################################################################################

resource "google_cloud_run_v2_service_iam_member" "public" {
  project  = google_cloud_run_v2_service.app.project
  location = google_cloud_run_v2_service.app.location
  name     = google_cloud_run_v2_service.app.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}
