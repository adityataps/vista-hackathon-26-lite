package com.payinvestigator.ingest.resolution;

import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * Binds the {@code payment-ingest.error-agent.*} keys from
 * application.properties, configuring the AI resolution agent that suggests
 * fixes for detected payment errors.
 */
@ConfigurationProperties(prefix = "payment-ingest.error-agent")
public class ErrorAgentProperties {

    /**
     * Location of the error-knowledge YAML file (detection + suggested
     * resolution per error code). Accepts any Spring resource location:
     * {@code classpath:...}, {@code file:C:/path/to/file.yaml}, or a bare
     * filesystem path. Defaults to the copy bundled in resources so the
     * agent works out of the box; override to point at an externally
     * maintained knowledge base without a rebuild.
     */
    private String knowledgeBasePath = "classpath:error-knowledge/agent_error_knowledge.yaml";

    /**
     * Where the agent appends its suggested-resolution write-up for every
     * payment that has one or more detected errors. A plain append-only
     * text log for now; swap {@link FileResolutionSink} for another
     * {@link ResolutionSink} implementation to route elsewhere later
     * (e.g. a queue, an API, or another DB table) without touching callers.
     */
    private String resolutionLogPath = "logs/error_resolution.log";

    /**
     * Bedrock model id (or inference-profile id) for the LLM that reasons
     * about detected errors and proposes a resolution. Defaults to Claude
     * Sonnet, matching the model the backend task role is granted access to
     * (see infra/iam.tf). Override if your account requires a region-
     * prefixed inference profile, e.g. {@code us.anthropic.claude-sonnet-4-6-v1:0}.
     */
    private String modelId = "anthropic.claude-sonnet-4-6-v1:0";

    /** AWS region for the Bedrock Runtime client. */
    private String bedrockRegion = "us-east-1";

    /** Max tokens the model may generate per resolution. */
    private int maxTokens = 1024;

    /** Sampling temperature - kept low since this is an analytical/triage task, not creative writing. */
    private double temperature = 0.1;

    /**
     * System prompt for the resolution agent. Accepts any Spring resource
     * location ({@code classpath:...} or {@code file:...}).
     */
    private String systemPromptPath = "classpath:prompts/error_resolution_system_prompt.md";

    public String getKnowledgeBasePath() {
        return knowledgeBasePath;
    }

    public void setKnowledgeBasePath(String knowledgeBasePath) {
        this.knowledgeBasePath = knowledgeBasePath;
    }

    public String getResolutionLogPath() {
        return resolutionLogPath;
    }

    public void setResolutionLogPath(String resolutionLogPath) {
        this.resolutionLogPath = resolutionLogPath;
    }

    public String getModelId() {
        return modelId;
    }

    public void setModelId(String modelId) {
        this.modelId = modelId;
    }

    public String getBedrockRegion() {
        return bedrockRegion;
    }

    public void setBedrockRegion(String bedrockRegion) {
        this.bedrockRegion = bedrockRegion;
    }

    public int getMaxTokens() {
        return maxTokens;
    }

    public void setMaxTokens(int maxTokens) {
        this.maxTokens = maxTokens;
    }

    public double getTemperature() {
        return temperature;
    }

    public void setTemperature(double temperature) {
        this.temperature = temperature;
    }

    public String getSystemPromptPath() {
        return systemPromptPath;
    }

    public void setSystemPromptPath(String systemPromptPath) {
        this.systemPromptPath = systemPromptPath;
    }
}
