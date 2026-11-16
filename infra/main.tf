# Azure definition for Document Q&A with Sources. Defined only, never deployed.
# See README.md in this folder for the reasoning behind each choice.

data "azurerm_client_config" "current" {}

locals {
  prefix = "${var.name}-${var.environment}"
  # Globally unique names (registry, key vault, database) get a short random suffix.
  unique = "${var.name}${var.environment}${random_string.suffix.result}"
  tags = {
    product     = var.name
    environment = var.environment
    cost-centre = var.cost_centre
    managed-by  = "terraform"
  }
  image = "${azurerm_container_registry.main.login_server}/${var.name}:${var.image_tag}"

  # Shared by the API and the worker: they run the same image with the same settings.
  app_env = merge({
    APP_USERNAME      = var.app_username
    RETRIEVER         = "bm25"
    ANSWERER          = var.enable_ai ? "llm" : "extractive"
    SUPPORT_THRESHOLD = tostring(var.support_threshold)
    }, var.enable_ai ? {
    # Azure OpenAI's OpenAI-compatible v1 endpoint; the model name is the deployment name.
    LLM_BASE_URL = "${azurerm_cognitive_account.ai[0].endpoint}openai/v1"
    LLM_MODEL    = azurerm_cognitive_deployment.ai[0].name
  } : {})

  app_secrets = merge({
    DATABASE_URL = azurerm_key_vault_secret.database_url.versionless_id
    APP_PASSWORD = azurerm_key_vault_secret.app_password.versionless_id
    SECRET_KEY   = azurerm_key_vault_secret.secret_key.versionless_id
    }, var.enable_ai ? {
    LLM_API_KEY = azurerm_key_vault_secret.llm_api_key[0].versionless_id
  } : {})
}

resource "random_string" "suffix" {
  length  = 5
  upper   = false
  special = false
}

resource "random_password" "database" {
  length  = 32
  special = false
}

resource "random_password" "app" {
  length  = 20
  special = false
}

resource "random_password" "secret_key" {
  length  = 48
  special = false
}

resource "azurerm_resource_group" "main" {
  name     = "rg-${local.prefix}"
  location = var.location
  tags     = local.tags
}

# ---------------------------------------------------------------- Observability

resource "azurerm_log_analytics_workspace" "main" {
  name                = "log-${local.prefix}"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  sku                 = "PerGB2018"
  retention_in_days   = 30
  tags                = local.tags
}

# ---------------------------------------------------------------- Identity and secrets

# One identity for both containers. It pulls the image and reads secrets; no passwords
# or registry credentials are stored in the app configuration.
resource "azurerm_user_assigned_identity" "app" {
  name                = "id-${local.prefix}"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  tags                = local.tags
}

resource "azurerm_key_vault" "main" {
  name                       = "kv-${local.unique}"
  resource_group_name        = azurerm_resource_group.main.name
  location                   = azurerm_resource_group.main.location
  tenant_id                  = data.azurerm_client_config.current.tenant_id
  sku_name                   = "standard"
  rbac_authorization_enabled = true
  purge_protection_enabled   = true
  soft_delete_retention_days = 7
  tags                       = local.tags
}

resource "azurerm_role_assignment" "deployer_secrets" {
  scope                = azurerm_key_vault.main.id
  role_definition_name = "Key Vault Secrets Officer"
  principal_id         = data.azurerm_client_config.current.object_id
}

resource "azurerm_role_assignment" "app_secrets" {
  scope                = azurerm_key_vault.main.id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_user_assigned_identity.app.principal_id
}

resource "azurerm_key_vault_secret" "database_url" {
  name         = "database-url"
  key_vault_id = azurerm_key_vault.main.id
  value = format("postgresql://%s:%s@%s:5432/%s?sslmode=require",
    azurerm_postgresql_flexible_server.main.administrator_login,
    random_password.database.result,
    azurerm_postgresql_flexible_server.main.fqdn,
  azurerm_postgresql_flexible_server_database.main.name)
  depends_on = [azurerm_role_assignment.deployer_secrets]
}

resource "azurerm_key_vault_secret" "app_password" {
  name         = "app-password"
  key_vault_id = azurerm_key_vault.main.id
  value        = random_password.app.result
  depends_on   = [azurerm_role_assignment.deployer_secrets]
}

resource "azurerm_key_vault_secret" "secret_key" {
  name         = "secret-key"
  key_vault_id = azurerm_key_vault.main.id
  value        = random_password.secret_key.result
  depends_on   = [azurerm_role_assignment.deployer_secrets]
}

# ---------------------------------------------------------------- Database

# Documents, passages and the job queue all live here, exactly as they do locally.
resource "azurerm_postgresql_flexible_server" "main" {
  name                          = "psql-${local.unique}"
  resource_group_name           = azurerm_resource_group.main.name
  location                      = azurerm_resource_group.main.location
  version                       = "16"
  sku_name                      = "B_Standard_B1ms"
  storage_mb                    = 32768
  administrator_login           = "docsqa"
  administrator_password        = random_password.database.result
  backup_retention_days         = 7
  public_network_access_enabled = true
  tags                          = local.tags

  lifecycle {
    ignore_changes = [zone]
  }
}

resource "azurerm_postgresql_flexible_server_database" "main" {
  name      = "docsqa"
  server_id = azurerm_postgresql_flexible_server.main.id
  charset   = "UTF8"
  collation = "en_US.utf8"
}

# Lets Azure-hosted services reach the database. Production should replace this with a
# private endpoint and VNet-integrated Container Apps (see README.md).
resource "azurerm_postgresql_flexible_server_firewall_rule" "azure_services" {
  name             = "allow-azure-services"
  server_id        = azurerm_postgresql_flexible_server.main.id
  start_ip_address = "0.0.0.0"
  end_ip_address   = "0.0.0.0"
}

# ---------------------------------------------------------------- Containers

resource "azurerm_container_registry" "main" {
  name                = "acr${local.unique}"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  sku                 = "Basic"
  admin_enabled       = false
  tags                = local.tags
}

resource "azurerm_role_assignment" "app_pull" {
  scope                = azurerm_container_registry.main.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.app.principal_id
}

resource "azurerm_container_app_environment" "main" {
  name                       = "cae-${local.prefix}"
  resource_group_name        = azurerm_resource_group.main.name
  location                   = azurerm_resource_group.main.location
  log_analytics_workspace_id = azurerm_log_analytics_workspace.main.id
  tags                       = local.tags
}

resource "azurerm_container_app" "api" {
  name                         = "ca-${local.prefix}-api"
  resource_group_name          = azurerm_resource_group.main.name
  container_app_environment_id = azurerm_container_app_environment.main.id
  revision_mode                = "Single"
  tags                         = local.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.app.id]
  }

  registry {
    server   = azurerm_container_registry.main.login_server
    identity = azurerm_user_assigned_identity.app.id
  }

  dynamic "secret" {
    for_each = local.app_secrets
    content {
      name                = lower(replace(secret.key, "_", "-"))
      key_vault_secret_id = secret.value
      identity            = azurerm_user_assigned_identity.app.id
    }
  }

  ingress {
    external_enabled = true
    target_port      = 8000
    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  template {
    min_replicas = 1
    max_replicas = 3

    container {
      name   = "api"
      image  = local.image
      cpu    = 0.5
      memory = "1Gi"

      dynamic "env" {
        for_each = local.app_env
        content {
          name  = env.key
          value = env.value
        }
      }

      dynamic "env" {
        for_each = local.app_secrets
        content {
          name        = env.key
          secret_name = lower(replace(env.key, "_", "-"))
        }
      }

      liveness_probe {
        transport = "HTTP"
        port      = 8000
        path      = "/api/health"
      }
    }

    http_scale_rule {
      name                = "http"
      concurrent_requests = "50"
    }
  }

  depends_on = [azurerm_role_assignment.app_pull, azurerm_role_assignment.app_secrets]
}

# The same image with a different command. No ingress: it only talks to the database.
resource "azurerm_container_app" "worker" {
  name                         = "ca-${local.prefix}-worker"
  resource_group_name          = azurerm_resource_group.main.name
  container_app_environment_id = azurerm_container_app_environment.main.id
  revision_mode                = "Single"
  tags                         = local.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.app.id]
  }

  registry {
    server   = azurerm_container_registry.main.login_server
    identity = azurerm_user_assigned_identity.app.id
  }

  dynamic "secret" {
    for_each = local.app_secrets
    content {
      name                = lower(replace(secret.key, "_", "-"))
      key_vault_secret_id = secret.value
      identity            = azurerm_user_assigned_identity.app.id
    }
  }

  template {
    # Jobs are claimed with SKIP LOCKED, so two workers never take the same job.
    min_replicas = 1
    max_replicas = 2

    container {
      name    = "worker"
      image   = local.image
      cpu     = 0.5
      memory  = "1Gi"
      command = ["python", "-m", "app.worker"]

      dynamic "env" {
        for_each = local.app_env
        content {
          name  = env.key
          value = env.value
        }
      }

      dynamic "env" {
        for_each = local.app_secrets
        content {
          name        = env.key
          secret_name = lower(replace(env.key, "_", "-"))
        }
      }
    }
  }

  depends_on = [azurerm_role_assignment.app_pull, azurerm_role_assignment.app_secrets]
}

# ---------------------------------------------------------------- Optional AI model

# Only created when enable_ai = true. The application's quote check still applies to
# everything the model writes.
resource "azurerm_cognitive_account" "ai" {
  count                 = var.enable_ai ? 1 : 0
  name                  = "oai-${local.unique}"
  resource_group_name   = azurerm_resource_group.main.name
  location              = azurerm_resource_group.main.location
  kind                  = "OpenAI"
  sku_name              = "S0"
  custom_subdomain_name = "oai-${local.unique}"
  tags                  = local.tags
}

resource "azurerm_cognitive_deployment" "ai" {
  count                = var.enable_ai ? 1 : 0
  name                 = var.ai_model.name
  cognitive_account_id = azurerm_cognitive_account.ai[0].id

  model {
    format  = "OpenAI"
    name    = var.ai_model.name
    version = var.ai_model.version
  }

  sku {
    name     = "GlobalStandard"
    capacity = 10
  }
}

resource "azurerm_key_vault_secret" "llm_api_key" {
  count        = var.enable_ai ? 1 : 0
  name         = "llm-api-key"
  key_vault_id = azurerm_key_vault.main.id
  value        = azurerm_cognitive_account.ai[0].primary_access_key
  depends_on   = [azurerm_role_assignment.deployer_secrets]
}
