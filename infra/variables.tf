variable "subscription_id" {
  description = "Azure subscription to deploy into."
  type        = string
}

variable "name" {
  description = "Short name used as a prefix for every resource."
  type        = string
  default     = "docsqa"
}

variable "environment" {
  description = "Environment name, e.g. dev, test, prod."
  type        = string
  default     = "dev"
}

variable "location" {
  description = "Azure region. Australia East keeps the data onshore."
  type        = string
  default     = "australiaeast"
}

variable "image_tag" {
  description = "Tag of the application image in the container registry."
  type        = string
  default     = "latest"
}

variable "app_username" {
  description = "Login name for the shared account."
  type        = string
  default     = "admin"
}

variable "support_threshold" {
  description = "Minimum share of a question a passage must cover to count as support."
  type        = number
  default     = 0.55
}

variable "cost_centre" {
  description = "Tag used to attribute spend to a team or product."
  type        = string
  default     = "knowledge-tools"
}

variable "enable_ai" {
  description = "Deploy an Azure OpenAI model and switch the answerer to it. Off by default: the system works without any model."
  type        = bool
  default     = false
}

variable "ai_model" {
  description = "Model deployed when enable_ai is true."
  type = object({
    name    = string
    version = string
  })
  default = {
    name    = "gpt-4o-mini"
    version = "2024-07-18"
  }
}
