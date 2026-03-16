################################################################################
# Input variables
################################################################################

variable "project_id" {
  description = "GCP project ID where all resources will be created."
  type        = string
}

variable "region" {
  description = "GCP region for Cloud Run and Artifact Registry."
  type        = string
  default     = "us-central1"
}

variable "app_name" {
  description = "Base name used for all resources (Cloud Run service, SA, secrets, repo)."
  type        = string
  default     = "workspace-agent"
}

variable "image_tag" {
  description = "Docker image tag to deploy (e.g. 'latest' or a git SHA)."
  type        = string
  default     = "latest"
}

variable "app_url" {
  description = "Public URL of the deployed app (used for OAuth redirect URI and CORS). Set after first deploy."
  type        = string
  default     = ""
}

variable "gemini_api_key" {
  description = "Gemini Developer API key (stored in Secret Manager)."
  type        = string
  sensitive   = true
}

variable "google_client_secrets_json" {
  description = "Full contents of the Google OAuth credentials.json file (stored in Secret Manager)."
  type        = string
  sensitive   = true
}

variable "gemini_model" {
  description = "Gemini model name to use for live voice sessions."
  type        = string
  default     = "gemini-2.5-flash-native-audio-preview-09-2025"
}

variable "min_instances" {
  description = "Minimum number of Cloud Run instances (0 = scale to zero when idle)."
  type        = number
  default     = 0
}

variable "max_instances" {
  description = "Maximum number of Cloud Run instances."
  type        = number
  default     = 10
}

variable "cpu" {
  description = "vCPU allocation per instance."
  type        = string
  default     = "1"
}

variable "memory" {
  description = "Memory allocation per instance."
  type        = string
  default     = "512Mi"
}

variable "log_level" {
  description = "Application log level (DEBUG, INFO, WARNING, ERROR)."
  type        = string
  default     = "INFO"
}
