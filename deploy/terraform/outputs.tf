################################################################################
# Outputs
################################################################################

output "service_url" {
  description = "Public URL of the deployed Cloud Run service."
  value       = google_cloud_run_v2_service.app.uri
}

output "image_repository" {
  description = "Full Artifact Registry repository path for docker push/pull."
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.app.repository_id}"
}

output "service_account_email" {
  description = "Email of the Cloud Run service account."
  value       = google_service_account.run_sa.email
}
