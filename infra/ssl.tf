# Per-hostname SSL/TLS mode override. The zone's default mode is "Full"
# (requires HTTPS at the origin), but the frontend's origin (S3 website
# endpoint, see dns.tf) is HTTP-only. This Configuration Rule overrides
# just this hostname to "Flexible" (viewer<->Cloudflare over HTTPS,
# Cloudflare<->origin over HTTP) so Cloudflare's free Universal SSL edge
# cert can be used without touching the zone-wide SSL mode (which would
# affect every other hostname on tapshalkar.com).
#
# NOTE: uses the modern Rulesets API (http_config_settings phase) rather
# than the legacy Page Rules API -- Page Rules require a Global API Key,
# they're not supported by scoped API tokens ("Page Rules endpoint does
# not support account owned tokens").
resource "cloudflare_ruleset" "frontend_flexible_ssl" {
  zone_id     = var.cloudflare_zone_id
  name        = "frontend-flexible-ssl"
  description = "Override SSL mode to Flexible for ${var.custom_domain} (HTTP-only S3 origin)"
  kind        = "zone"
  phase       = "http_config_settings"

  rules {
    ref         = "frontend_flexible_ssl"
    description = "Flexible SSL for the frontend custom domain"
    expression  = "(http.host eq \"${var.custom_domain}\")"
    action      = "set_config"
    enabled     = true

    action_parameters {
      ssl = "flexible"
    }
  }
}
