export type EditionKind = "local" | "openrouter" | "generic";

/**
 * The API reports `edition_profile` as `omos-<edition>-v<release>`, where the
 * release segment is stamped from the backend's canonical version. Pinning a
 * full literal here would strand every user whose stored profile came from an
 * earlier beta, so the edition slug is matched exactly and only the release
 * segment is matched structurally: dotted numeric components with an optional
 * pre-release and/or build suffix. Anything else — an unknown edition, a
 * truncated ID, a non-string, or `null` — stays `generic` and shows the neutral
 * copy instead of another edition's setup instructions.
 */
const PROFILE_ID_PATTERN =
  /^omos-(local|openrouter)-v\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$/;

export function getEditionKind(profileId: unknown): EditionKind {
  if (typeof profileId !== "string") return "generic";
  const match = PROFILE_ID_PATTERN.exec(profileId);
  return match ? (match[1] as EditionKind) : "generic";
}

export function getStartHereProfileCopy(profileId: unknown) {
  switch (getEditionKind(profileId)) {
    case "local":
      return {
        kind: "local" as const,
        title: "Set up Local AI",
        text: "This edition runs Account Manager inference on your PC with the OMOS-managed llama.cpp runtime. No provider API key is required. In Settings → Local Model, review the pinned Qwen model and disk estimate, then explicitly download it. OMOS verifies the files before activation; start the runtime when setup is complete.",
        stepTitle: "Set up Local AI",
        stepText: "Review and explicitly download the pinned model, then start the verified managed runtime. No provider key is required.",
        stepAction: "Open Local Model setup",
        settingsSection: "local",
        troubleshooting: "Local model not ready: open Settings → Local Model, confirm checksum verification and activation, then start the runtime. No AI provider key is required for Local inference.",
        showCloudSetupImage: false,
      };
    case "openrouter":
      return {
        kind: "openrouter" as const,
        title: "Set up OpenRouter",
        text: "This edition starts with AUTO routing, Local inference disabled, and OpenRouter selected as its cloud default. Enter your own OpenRouter key in Settings → AI & Models; the OS credential vault protects saved keys. No Local model download or runtime setup is required. Prompts and evidence selected for a turn are sent to OpenRouter.",
        stepTitle: "Connect OpenRouter",
        stepText: "Enter and test your own OpenRouter key. AUTO uses the configured cloud route; Local model setup is not required.",
        stepAction: "Open OpenRouter settings",
        settingsSection: "ai",
        troubleshooting: "OpenRouter not connected: open Settings → AI & Models, enter your own key, then test the connection. AUTO needs a configured OpenRouter credential in this edition.",
        showCloudSetupImage: true,
      };
    default:
      return {
        kind: "generic" as const,
        title: "Set up AI",
        text: "Use Settings → AI & Models to review the AI route configured for this installation. If Local inference is enabled, open Local Model to review and explicitly download its pinned model; otherwise configure the cloud provider required by your route. Cloud inference sends the prompt and evidence selected for a turn to that provider.",
        stepTitle: "Review AI setup",
        stepText: "Check the installed edition's AI route and complete the setup shown in Settings.",
        stepAction: "Open AI settings",
        settingsSection: "ai",
        troubleshooting: "AI setup issue: open Settings → AI & Models and check the route and provider status. If Local is enabled, verify the model and runtime in Settings → Local Model.",
        showCloudSetupImage: false,
      };
  }
}

export function getAiSettingsProfileCopy(profileId: unknown) {
  switch (getEditionKind(profileId)) {
    case "local":
      return {
        kind: "local" as const,
        localModelAvailable: true,
        summaryTitle: "Local edition setup",
        summaryText: "Account Manager can run on this PC with the managed Local runtime. No provider API key is required. Open the Local Model tab to review the pinned Qwen model, explicitly download it, verify it, and start the runtime. AUTO uses the ready Local model; cloud escalation starts disabled.",
        routingText: "In this edition, AUTO uses the verified Local model when its managed runtime is ready. Cloud escalation is disabled by default.",
        autoText: "Uses the ready Local model. Cloud fallback remains disabled unless you change the setting and configure a provider.",
        openrouterText: "Optional cloud inference through OpenRouter. If you configure it, prompts and evidence selected for a turn leave this PC.",
      };
    case "openrouter":
      return {
        kind: "openrouter" as const,
        localModelAvailable: false,
        summaryTitle: "OpenRouter edition setup",
        summaryText: "This edition starts with AUTO routing, Local inference disabled, and OpenRouter as its cloud default. Enter and test your own OpenRouter key below; the OS credential vault protects saved keys. Prompts and evidence selected for each turn are sent to OpenRouter. Downloading or starting a Local model is not required.",
        routingText: "This edition starts with AUTO routing to the configured cloud provider and Local inference disabled. OpenRouter requires your own saved credential.",
        autoText: "Uses the configured cloud provider because Local inference is disabled in this edition. Set up your OpenRouter credential below.",
        openrouterText: "Enter your own OpenRouter key here. OMOS saves it in the OS credential vault; prompts and evidence selected for a turn are sent to OpenRouter for cloud inference.",
      };
    default:
      return {
        kind: "generic" as const,
        localModelAvailable: true,
        summaryTitle: "AI setup",
        summaryText: "Review the route configured for this installation. Local model setup is available only when Local inference is enabled; model downloads require an explicit action in the Local Model tab.",
        routingText: "Choose how OMOS routes model requests. Local routing is available when enabled and the verified model runtime is ready.",
        autoText: "Uses the ready Local model when available, then follows the configured provider fallback.",
        openrouterText: "Optional cloud inference through OpenRouter. Requests routed through OpenRouter leave the local machine.",
      };
  }
}
