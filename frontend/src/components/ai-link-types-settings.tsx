import { useEffect, useState } from "react";
import { useInternalLinkSettings, useSaveInternalLinkSettings } from "../hooks/use-internal-links";
import { Button } from "./ui/button";

export function AiLinkTypeFields({ value, onChange }: { value: string[]; onChange: (types: string[]) => void }) {
  return <fieldset className="space-y-2">
    <legend className="text-sm font-medium">AI link suggestions</legend>
    <p className="text-xs text-muted-foreground">Allow AI to propose one link or one added sentence for these source types. Every apply requires a live preview. Collections are off by default.</p>
    <div className="flex flex-wrap gap-4">
      {[["product", "Products"], ["blog_article", "Blog articles"], ["collection", "Collections"]].map(([type, label]) =>
        <label key={type} className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={value.includes(type)} onChange={(e) => onChange(e.target.checked ? [...value, type] : value.filter(t => t !== type))} />
          {label}
        </label>)}
    </div>
    <p className="text-xs text-muted-foreground">Pages can receive links; they are not suggestion sources.</p>
  </fieldset>;
}

export function AiLinkTypesSettings() {
  const settings = useInternalLinkSettings();
  const save = useSaveInternalLinkSettings();
  const [types, setTypes] = useState<string[]>([]);
  useEffect(() => { if (settings.data) setTypes(settings.data.ai_woven_enabled_types); }, [settings.data]);
  return <section className="space-y-4 rounded-xl border bg-white p-5">
    <h3 className="font-medium">Internal link safety</h3>
    {settings.data ? <><AiLinkTypeFields value={types} onChange={setTypes} />
      <Button disabled={save.isPending} onClick={() => save.mutate({ ai_woven_enabled_types: types })}>Save link settings</Button>
    </> : <p>{settings.error ? "Could not load link settings." : "Loading link settings…"}</p>}
    {save.isSuccess && <p role="status" className="text-sm">Link settings saved.</p>}
    {save.error && <p role="alert" className="text-sm text-red-700">{save.error.message}</p>}
  </section>;
}
