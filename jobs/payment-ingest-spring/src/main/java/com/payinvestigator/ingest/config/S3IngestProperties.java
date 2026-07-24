package com.payinvestigator.ingest.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * Binds the {@code payment-ingest.s3.*} keys from application.properties.
 * All S3 settings for the poller live here so they can be overridden per
 * environment without touching code.
 */
@ConfigurationProperties(prefix = "payment-ingest.s3")
public class S3IngestProperties {

    /** AWS region for the S3 client, e.g. us-east-1. */
    private String region = "us-east-1";

    /** Bucket to poll for new payment XML files. */
    private String bucket;

    /** Key prefix under which payment files are dropped, e.g. "payments/". */
    private String paymentsPrefix = "payments/";

    /** Optional key prefix for reference data (bic_directory.json /
     * watchlist.json / closed_accounts.json). Leave blank to disable those checks. */
    private String referenceDataPrefix = "reference/";

    /** Polling interval, in milliseconds. */
    private long pollIntervalMs = 15_000;

    /** Optional custom S3 endpoint (e.g. for LocalStack/MinIO in local dev). */
    private String endpointOverride;

    public String getRegion() {
        return region;
    }

    public void setRegion(String region) {
        this.region = region;
    }

    public String getBucket() {
        return bucket;
    }

    public void setBucket(String bucket) {
        this.bucket = bucket;
    }

    public String getPaymentsPrefix() {
        return paymentsPrefix;
    }

    public void setPaymentsPrefix(String paymentsPrefix) {
        this.paymentsPrefix = paymentsPrefix;
    }

    public String getReferenceDataPrefix() {
        return referenceDataPrefix;
    }

    public void setReferenceDataPrefix(String referenceDataPrefix) {
        this.referenceDataPrefix = referenceDataPrefix;
    }

    public long getPollIntervalMs() {
        return pollIntervalMs;
    }

    public void setPollIntervalMs(long pollIntervalMs) {
        this.pollIntervalMs = pollIntervalMs;
    }

    public String getEndpointOverride() {
        return endpointOverride;
    }

    public void setEndpointOverride(String endpointOverride) {
        this.endpointOverride = endpointOverride;
    }
}
