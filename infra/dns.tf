# Custom domain for the frontend, served via S3 static website hosting and
# proxied through Cloudflare for HTTPS. Cloudflare's Universal SSL issues a
# free edge cert automatically for proxied (orange-cloud) records, so no
# ACM cert / CloudFront distribution is needed.
#
# The zone's default SSL/TLS mode is "Full" (requires HTTPS at the origin),
# but the S3 website endpoint is HTTP-only. Rather than change the
# zone-wide mode (would affect every other hostname on tapshalkar.com),
# ssl.tf adds a Configuration Rule that overrides the mode to "Flexible"
# (viewer<->Cloudflare HTTPS, Cloudflare<->origin HTTP) for just this
# hostname.
resource "cloudflare_record" "frontend" {
  zone_id = var.cloudflare_zone_id
  name    = "vistahack26"
  content = "${aws_s3_bucket.frontend.bucket}.s3-website-${var.region}.amazonaws.com"
  type    = "CNAME"
  ttl     = 1 # required to be 1 ("automatic") when proxied
  proxied = true
}
