/**
 * @deprecated Import `SyncQueueTable` / `SyncQueueDetailItem` from `./sync-queue-table` instead.
 */
export type { SyncQueueDetailItem as PagespeedQueueDetailItem } from "./sync-queue-table";
import { SyncQueueTable, type SyncQueueDetailItem } from "./sync-queue-table";

type Props = { items: SyncQueueDetailItem[] };

/** PageSpeed-only preset title for the shared queue table. */
export function PageSpeedQueueTable({ items }: Props) {
  return <SyncQueueTable title="Queue Stream" items={items} />;
}
