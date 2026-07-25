# Custom domain for the frontend, served via S3 static website hosting.
#
# NOTE: the S3 website endpoint is HTTP-only (S3 does not support HTTPS on
# website endpoints), and this Cloudflare zone's SSL/TLS mode is "Full" —
# proxying (orange-cloud) a plain-HTTP origin under "Full" mode would break
# with a 526 origin SSL error. Rather than change the zone-wide SSL mode
# (which would affect every other hostname on tapshalkar.com), this record
# is DNS-only (grey cloud): a plain CNAME straight to the S3 website
# endpoint, served over HTTP — matching the raw S3 URL's current behavior.
# If HTTPS on the custom domain is needed later, put CloudFront in front of
# the bucket and proxy through Cloudflare instead.
resource "cloudflare_record" "frontend" {
  zone_id = var.cloudflare_zone_id
  name    = "vistahack26"
  content = "${aws_s3_bucket.frontend.bucket}.s3-website-${var.region}.amazonaws.com"
  type    = "CNAME"
  ttl     = 300
  proxied = false
}
