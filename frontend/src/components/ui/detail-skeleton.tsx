import { Skeleton } from "./skeleton";

export function DetailPageSkeleton({ showFooter = true }: { showFooter?: boolean }) {
  return (
    <div className="space-y-6 pb-10">
      <Skeleton className="h-5 w-24 rounded-lg" />
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-5">
        {Array.from({ length: 5 }).map((_, i) => (
          <Skeleton key={i} className="h-32 rounded-[24px]" />
        ))}
      </div>
      <Skeleton className="h-12 rounded-[24px]" />
      <Skeleton className="h-[480px] rounded-[24px]" />
      {showFooter && <Skeleton className="h-48 rounded-[24px]" />}
    </div>
  );
}
