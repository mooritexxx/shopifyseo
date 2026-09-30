import { Button } from "./ui/button";

export function BodyDraftConflict({ body, onUseLatest }: { body: string; onUseLatest: () => void }) {
  return <div role="alert" className="space-y-2 rounded border border-amber-300 bg-amber-50 p-4 text-sm">
    <p>The saved body changed while you were editing. Your draft is preserved. Review the latest body before saving.</p>
    <details><summary className="cursor-pointer">Latest saved body</summary><pre className="max-h-52 overflow-auto whitespace-pre-wrap text-xs">{body}</pre></details>
    <Button variant="outline" onClick={onUseLatest}>Replace my body draft with the latest saved body</Button>
  </div>;
}
