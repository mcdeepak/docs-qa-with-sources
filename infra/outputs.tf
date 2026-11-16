output "app_url" {
  description = "Public address of the application."
  value       = "https://${azurerm_container_app.api.ingress[0].fqdn}"
}

output "container_registry" {
  description = "Push the application image here."
  value       = azurerm_container_registry.main.login_server
}

output "key_vault" {
  description = "Holds the database URL, the login password and the session key."
  value       = azurerm_key_vault.main.name
}
