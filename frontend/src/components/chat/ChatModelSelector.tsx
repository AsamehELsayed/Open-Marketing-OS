import type { ChatModelCatalog, ChatModelProvider, ChatModelSelection } from "../../api/client";

const SEPARATOR = "\u001f";

function selectionValue(selection: ChatModelSelection): string {
  return `${selection.model_provider}${SEPARATOR}${selection.model_id ?? ""}`;
}

function readSelection(value: string): ChatModelSelection | null {
  const splitAt = value.indexOf(SEPARATOR);
  if (splitAt < 0) return null;
  const provider = value.slice(0, splitAt) as ChatModelProvider;
  if (provider !== "AUTO" && provider !== "LOCAL" && provider !== "OPENROUTER") return null;
  return { model_provider: provider, model_id: value.slice(splitAt + 1) };
}

export default function ChatModelSelector({
  catalog,
  selection,
  loading,
  saving,
  error,
  onChange,
}: {
  catalog: ChatModelCatalog | null;
  selection: ChatModelSelection | null;
  loading: boolean;
  saving: boolean;
  error: string | null;
  onChange: (selection: ChatModelSelection) => void;
}) {
  const providers = catalog?.providers ?? [];
  const selectedProvider = providers.find((provider) => provider.provider === selection?.model_provider);
  const selectedModelAvailable = selection?.model_provider === "AUTO"
    ? true
    : Boolean(selectedProvider?.available && selectedProvider.models.some((model) => model.id === selection?.model_id));
  const unavailable = providers.filter((provider) => !provider.available);
  const showCatalogDetail = Boolean(selectedProvider &&
    ((!selectedProvider.available) || selectedProvider.models.length === 0));

  return (
    <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-meta">
      <label htmlFor="chat-model-selection" className="shrink-0 font-medium text-inksecondary">Model</label>
      <select
        id="chat-model-selection"
        aria-label="Chat model"
        value={selection ? selectionValue(selection) : ""}
        disabled={loading || saving || !catalog}
        onChange={(event) => {
          const next = readSelection(event.target.value);
          if (next) onChange(next);
        }}
        className="max-w-full min-w-48 rounded-sm border border-linedefault bg-base px-2 py-1.5 text-meta text-ink focus:border-accent focus:outline-none disabled:opacity-60"
      >
        {!selection && <option value="">{loading ? "Loading models…" : "Model unavailable"}</option>}
        {providers.map((provider) => (
          <optgroup key={provider.provider} label={provider.provider}>
            {provider.available && provider.models.length > 0 ? provider.models.map((model) => (
              <option key={`${provider.provider}:${model.id}`} value={`${provider.provider}${SEPARATOR}${model.id}`}>
                {model.name}{model.id ? ` · ${model.id}` : ""}
              </option>
            )) : (
              <option
                key={`${provider.provider}:unavailable`}
                value={`${provider.provider}${SEPARATOR}${provider.provider === selection?.model_provider ? selection.model_id ?? "" : ""}`}
                disabled={!provider.available || provider.models.length === 0}
              >
                {provider.available ? "No models available" : "Unavailable"}
              </option>
            )}
            {selection?.model_provider === provider.provider && provider.available && !selectedModelAvailable && (
              <option value={selectionValue(selection)} disabled>
                {selection.model_id || "Selected model"} (unavailable)
              </option>
            )}
          </optgroup>
        ))}
      </select>
      {saving && <span role="status" className="text-inkmuted">Saving…</span>}
      {error && <span role="alert" className="text-err">{error}</span>}
      {!error && showCatalogDetail && selectedProvider && (
        <span role="status" className={selectedProvider.available ? "text-inkmuted" : "text-err"}>
          {selectedProvider.detail || (selectedProvider.available ? "No configured models are available." : `${selectedProvider.provider} is unavailable.`)}
        </span>
      )}
      {!error && !showCatalogDetail && unavailable.length > 0 && (
        <span role="status" className="text-inkmuted">
          {unavailable.map((provider) => `${provider.provider}: ${provider.detail || "unavailable"}`).join(" · ")}
        </span>
      )}
    </div>
  );
}
