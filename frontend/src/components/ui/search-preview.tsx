export function SearchPreview({
  title,
  url,
  description
}: {
  title: string;
  url: string;
  description: string;
}) {
  return (
    <div className="rounded-[24px] border border-[#dbe5f3] bg-[linear-gradient(180deg,#fbfdff_0%,#f4f9ff_100%)] p-4">
      <p className="text-xs uppercase tracking-[0.16em] text-slate-500">Search preview</p>
      <p className="mt-3 text-sm text-[#155eef]">{title || "Untitled"}</p>
      <p className="mt-1 text-xs text-[#12805c]">{url}</p>
      <p className="mt-2 text-sm leading-6 text-slate-600">{description || "Your meta description preview will appear here."}</p>
    </div>
  );
}
