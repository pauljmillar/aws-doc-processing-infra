variable "project_name" {
  type    = string
  default = "docproc"
}

variable "region" {
  type    = string
  default = "us-west-2"
}

variable "openai_secret_value" {
  type      = string
  sensitive = true
  description = "Your OpenAI API key (e.g. sk-...)"
}
