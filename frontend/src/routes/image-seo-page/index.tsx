import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Check,
  CircleAlert,
  Image as ImageIcon,
  Loader2,
  Search,
  X
} from "lucide-react";

import { Button } from "../../components/ui/button";
import { Card, CardContent } from "../../components/ui/card";
import { Checkbox } from "../../components/ui/checkbox";
import { ImageComparisonSlider } from "../../components/ui/image-comparison-slider-horizontal";
import { Input } from "../../components/ui/input";
import { Label } from "../../components/ui/label";
import { Modal } from "../../components/ui/modal";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue
} from "../../components/ui/select";
import { ImageSeoOptimizeProgressPanel } from "../../components/image-seo-optimize-progress-panel";
import { SummaryCard } from "../../components/ui/summary-card";
import { Table, TableBody, TableHead, TableHeader, TableRow } from "../../components/ui/table";
import { Toast } from "../../components/ui/toast";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "../../components/ui/tooltip";
import { getJson, postJson } from "../../lib/api";
import { cn, formatNumber } from "../../lib/utils";
import {
  type CatalogImageSeoRow,
  productImageSeoDraftResultSchema,
  productImageSeoListSchema,
  productImageSeoOptimizeResultSchema,
  type ProductImageSeoDraftResult,
  type ProductImageSeoOptimizeResult
} from "../../types/api";
import { BatchOptimizeModal, type BatchEntry, type BatchItemStatus } from "./BatchOptimizeModal";
import { ImageSeoSortableTh, ImageSeoTableRow } from "./ImageSeoTableRow";
import {
  BATCH_CONCURRENCY,
  COMPARISON_NO_CHANGE,
  PAGE_SIZE_OPTIONS,
  createPool,
  fileReductionPercent,
  filenameFromUrl,
  formatBytes,
  imageFormatLabelFromFilename,
  RESOURCE_TYPE_LABEL,
  type ImageSeoListSort,
} from "./utils";

type ImageModalPhase = "form" | "running" | "success" | "error";

export function ImageSeoPage() {
  const queryClient = useQueryClient();
  const [productQuery, setProductQuery] = useState("");
  const [resourceTypeFilter, setResourceTypeFilter] = useState<string>("all");
  const [statusFilter, setStatusFilter] = useState<"all" | "optimized" | "not_optimized">("all");
  const [pageSize, setPageSize] = useState<(typeof PAGE_SIZE_OPTIONS)[number]>(50);
  const [page, setPage] = useState(0);
  const [sortColumn, setSortColumn] = useState<ImageSeoListSort>("handle");
  const [sortDirection, setSortDirection] = useState<"asc" | "desc">("asc");
  const [toast, setToast] = useState<{ message: string; variant: "success" | "error" | "info" } | null>(null);

  // --- Row selection for batch optimize ---
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [batchOpen, setBatchOpen] = useState(false);
  const [batchQueue, setBatchQueue] = useState<BatchEntry[]>([]);
  const [batchRunning, setBatchRunning] = useState(false);
  const batchAbortRef = useRef(false);
  const pauseUntilRef = useRef(0);

  const toggleRowSelection = useCallback((id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const [modalRow, setModalRow] = useState<CatalogImageSeoRow | null>(null);
  const [altEdit, setAltEdit] = useState("");
  /** Catalog alt when modal opened — shown in "Current" column and baseline for vision merge. */
  const [catalogAltAtOpen, setCatalogAltAtOpen] = useState("");
  /** Catalog alt when the modal was opened — used to decide whether Optimize should use vision draft alt vs user edits. */
  const openedAltBaselineRef = useRef("");
  /** Keeps latest textarea value across async draft (avoids stale closure). */
  const altEditRef = useRef("");
  const [pipelinePhase, setPipelinePhase] = useState<"idle" | "draft" | "shopify">("idle");
  const [imageModalPhase, setImageModalPhase] = useState<ImageModalPhase>("form");
  const [optimizeRunKey, setOptimizeRunKey] = useState(0);
  const [optimizeDone, setOptimizeDone] = useState<{
    draft: ProductImageSeoDraftResult;
    opt: ProductImageSeoOptimizeResult;
    beforeAlt: string;
    beforeFilename: string;
  } | null>(null);
  /** Set after draft POST succeeds — has original_size_bytes before optimize finishes. */
  const [pendingDraft, setPendingDraft] = useState<ProductImageSeoDraftResult | null>(null);
  const [optimizeErrorMsg, setOptimizeErrorMsg] = useState<string | null>(null);

  const pipelineRunning = imageModalPhase === "running";

  altEditRef.current = altEdit;

  /** Draft uses catalog flags — no extra checkboxes. Optimize runs draft then Shopify apply. */
  const optimizeActions = useMemo(() => {
    if (!modalRow) {
      return { applyFilename: false, convertWebp: false, willReupload: false };
    }
    const f = modalRow.flags;
    const applyFilename = Boolean(f.weak_filename || f.seo_filename_mismatch);
    const convertWebp = Boolean(f.not_webp);
    return {
      applyFilename,
      convertWebp,
      willReupload: applyFilename || convertWebp
    };
  }, [modalRow]);

  const draftForMetrics = optimizeDone?.draft ?? pendingDraft;

  /** Current column: size only known after the draft step downloads the file (catalog has no byte size). */
  const comparisonCurrent = useMemo(() => {
    if (!modalRow) {
      return { filename: "", format: "—", size: "—" };
    }
    const fn = filenameFromUrl(modalRow.url);
    const bytes = draftForMetrics?.original_size_bytes;
    return {
      filename: fn,
      format: imageFormatLabelFromFilename(fn),
      size: bytes != null ? formatBytes(bytes) : "—"
    };
  }, [modalRow, draftForMetrics?.original_size_bytes]);

  /** Preview vs applied values for the New column (only New updates after success). */
  const comparisonNew = useMemo(() => {
    if (!modalRow) {
      return {
        filename: "—",
        format: "—",
        size: "—",
        alt: "—" as string,
        reductionPct: null as number | null
      };
    }
    const catalogFn = filenameFromUrl(modalRow.url);
    const currentFn =
      optimizeActions.willReupload && modalRow.suggested_filename_webp.trim()
        ? modalRow.suggested_filename_webp.trim()
        : catalogFn;

    if (imageModalPhase === "success" && optimizeDone) {
      const { opt, draft, beforeAlt, beforeFilename } = optimizeDone;
      const orig = draft.original_size_bytes;
      const nextBytes = draft.draft_size_bytes;
      const fileActuallyChanged = Boolean(
        (opt.applied_filename && opt.applied_filename.trim()) ||
          (opt.new_image_url && opt.new_image_url.trim())
      );
      const appliedAlt = (opt.applied_alt || "").trim();
      const altLine =
        appliedAlt === (beforeAlt || "").trim()
          ? COMPARISON_NO_CHANGE
          : appliedAlt || "—";

      if (!fileActuallyChanged) {
        return {
          filename: COMPARISON_NO_CHANGE,
          format: COMPARISON_NO_CHANGE,
          size: COMPARISON_NO_CHANGE,
          alt: altLine,
          reductionPct: null
        };
      }

      const appliedFn =
        opt.applied_filename?.trim() ||
        (opt.new_image_url ? filenameFromUrl(opt.new_image_url) : "") ||
        "";
      const filenameLine = appliedFn && appliedFn !== beforeFilename ? appliedFn : COMPARISON_NO_CHANGE;
      const beforeFmt = imageFormatLabelFromFilename(beforeFilename);
      const afterFmt = appliedFn ? imageFormatLabelFromFilename(appliedFn) : beforeFmt;
      const formatLine = afterFmt !== beforeFmt ? afterFmt : COMPARISON_NO_CHANGE;
      const sizeLine =
        orig != null && nextBytes != null && orig !== nextBytes ? formatBytes(nextBytes) : COMPARISON_NO_CHANGE;
      const reductionPct =
        orig != null && nextBytes != null && nextBytes < orig
          ? fileReductionPercent(orig, nextBytes)
          : null;

      return {
        filename: filenameLine,
        format: formatLine,
        size: sizeLine,
        alt: altLine,
        reductionPct: sizeLine !== COMPARISON_NO_CHANGE ? reductionPct : null
      };
    }

    if (pendingDraft && imageModalPhase === "running") {
      const d = pendingDraft;
      if (!optimizeActions.willReupload) {
        return {
          filename: COMPARISON_NO_CHANGE,
          format: COMPARISON_NO_CHANGE,
          size: COMPARISON_NO_CHANGE,
          alt: "—",
          reductionPct: null
        };
      }
      const previewDraftFn = (d.draft_filename || "").trim() || currentFn;
      const filenameLine = previewDraftFn !== catalogFn ? previewDraftFn : COMPARISON_NO_CHANGE;
      const beforeFmt = imageFormatLabelFromFilename(catalogFn);
      const afterFmt = imageFormatLabelFromFilename(previewDraftFn);
      const formatLine = afterFmt !== beforeFmt ? afterFmt : COMPARISON_NO_CHANGE;
      const sizeLine =
        d.original_size_bytes !== d.draft_size_bytes ? formatBytes(d.draft_size_bytes) : COMPARISON_NO_CHANGE;
      const reductionPct =
        d.draft_size_bytes < d.original_size_bytes
          ? fileReductionPercent(d.original_size_bytes, d.draft_size_bytes)
          : null;
      return {
        filename: filenameLine,
        format: formatLine,
        size: sizeLine,
        alt: "—",
        reductionPct: sizeLine !== COMPARISON_NO_CHANGE ? reductionPct : null
      };
    }

    if (!optimizeActions.willReupload) {
      return {
        filename: COMPARISON_NO_CHANGE,
        format: COMPARISON_NO_CHANGE,
        size: COMPARISON_NO_CHANGE,
        alt: "—",
        reductionPct: null
      };
    }
    const previewFmt = imageFormatLabelFromFilename(currentFn);
    return {
      filename: currentFn,
      format: previewFmt,
      size: "—",
      alt: "—",
      reductionPct: null
    };
  }, [
    modalRow,
    imageModalPhase,
    optimizeDone,
    optimizeActions.willReupload,
    pendingDraft
  ]);

  const optimizeProgressStatus = useMemo(() => {
    if (imageModalPhase === "running") return "running" as const;
    if (imageModalPhase === "success") return "complete" as const;
    return "idle" as const;
  }, [imageModalPhase]);

  const optimizeProgressLatest = useMemo(() => {
    if (imageModalPhase === "running") {
      return pipelinePhase === "shopify"
        ? "Sending updates to Shopify (alt and media replacement if needed)…"
        : "Downloading the image, preparing alt, filename, and encoding…";
    }
    if (imageModalPhase === "success" && optimizeDone) {
      return optimizeDone.opt.message;
    }
    return undefined;
  }, [imageModalPhase, pipelinePhase, optimizeDone]);

  const modalComparisonNewUrl = (optimizeDone?.opt.new_image_url ?? "").trim();
  const showModalImageComparison =
    !!modalRow &&
    imageModalPhase === "success" &&
    Boolean(modalComparisonNewUrl) &&
    modalComparisonNewUrl !== modalRow.url.trim();
  const showModalAltOnlySquare =
    !!modalRow && imageModalPhase === "success" && !showModalImageComparison;

  const listUrl = useMemo(() => {
    const p = new URLSearchParams();
    p.set("limit", String(pageSize));
    p.set("offset", String(page * pageSize));
    p.set("sort", sortColumn);
    p.set("direction", sortDirection);
    if (productQuery.trim()) p.set("product_query", productQuery.trim());
    if (resourceTypeFilter && resourceTypeFilter !== "all") p.set("resource_type", resourceTypeFilter);
    if (statusFilter !== "all") p.set("status", statusFilter);
    return `/api/image-seo/product-images?${p.toString()}`;
  }, [page, pageSize, productQuery, resourceTypeFilter, statusFilter, sortColumn, sortDirection]);

  function toggleSortColumn(col: ImageSeoListSort) {
    setPage(0);
    if (sortColumn === col) {
      setSortDirection((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortColumn(col);
      setSortDirection("asc");
    }
  }

  const { data, isLoading, error } = useQuery({
    queryKey: ["image-seo-product-images", listUrl],
    queryFn: () => getJson(listUrl, productImageSeoListSchema)
  });

  const selectableOnPage = useMemo(
    () => (data?.items ?? []).filter((r) => r.optimize_supported),
    [data?.items]
  );

  const allPageSelected =
    selectableOnPage.length > 0 && selectableOnPage.every((r) => selectedIds.has(r.image_row_id));

  const toggleSelectAll = useCallback(() => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (allPageSelected) {
        for (const r of selectableOnPage) next.delete(r.image_row_id);
      } else {
        for (const r of selectableOnPage) next.add(r.image_row_id);
      }
      return next;
    });
  }, [allPageSelected, selectableOnPage]);

  useEffect(() => {
    if (!modalRow) {
      setPipelinePhase("idle");
      setImageModalPhase("form");
      setOptimizeDone(null);
      setPendingDraft(null);
      setOptimizeErrorMsg(null);
    }
  }, [modalRow]);

  function closeImageModal() {
    void queryClient.invalidateQueries({ queryKey: ["image-seo-product-images"] });
    setModalRow(null);
  }

  function openModal(row: CatalogImageSeoRow) {
    const alt = row.alt_text || "";
    openedAltBaselineRef.current = alt;
    setCatalogAltAtOpen(alt);
    setImageModalPhase("form");
    setOptimizeDone(null);
    setPendingDraft(null);
    setOptimizeErrorMsg(null);
    setModalRow(row);
    setAltEdit(alt);
  }

  async function runOptimizePipeline() {
    if (!modalRow?.optimize_supported) return;
    const rowSnapshot = modalRow;
    const beforeFilename = filenameFromUrl(rowSnapshot.url);
    const beforeAltSnapshot = openedAltBaselineRef.current;

    setOptimizeRunKey((k) => k + 1);
    setPendingDraft(null);
    setImageModalPhase("running");
    setOptimizeErrorMsg(null);
    setPipelinePhase("draft");
    try {
      const draft = await postJson("/api/image-seo/product-images/draft", productImageSeoDraftResultSchema, {
        product_shopify_id: rowSnapshot.product_shopify_id,
        image_shopify_id: rowSnapshot.image_shopify_id,
        apply_suggested_filename: optimizeActions.applyFilename,
        convert_webp: optimizeActions.convertWebp,
        auto_vision_alt: true
      });
      if (!draft.ok) {
        setOptimizeErrorMsg(draft.message);
        setImageModalPhase("error");
        setToast({ message: draft.message, variant: "error" });
        return;
      }
      setPendingDraft(draft);
      const baseline = openedAltBaselineRef.current.trim();
      const currentAlt = altEditRef.current.trim();
      const finalAlt = currentAlt === baseline ? draft.draft_alt : currentAlt;

      setPipelinePhase("shopify");
      const opt = await postJson("/api/image-seo/product-images/optimize", productImageSeoOptimizeResultSchema, {
        product_shopify_id: rowSnapshot.product_shopify_id,
        image_shopify_id: rowSnapshot.image_shopify_id,
        apply_suggested_alt: true,
        apply_suggested_filename: optimizeActions.applyFilename,
        convert_webp: optimizeActions.convertWebp,
        alt_override: finalAlt,
        dry_run: false
      });
      if (!opt.ok) {
        setOptimizeErrorMsg(opt.message);
        setImageModalPhase("error");
        setToast({ message: opt.message, variant: "error" });
        return;
      }
      setOptimizeDone({ draft, opt, beforeAlt: beforeAltSnapshot, beforeFilename });
      setImageModalPhase("success");
      setToast({ message: opt.message, variant: "success" });
      void queryClient.invalidateQueries({ queryKey: ["image-seo-product-images"] });
    } catch (e) {
      const msg = (e as Error).message;
      setOptimizeErrorMsg(msg);
      setImageModalPhase("error");
      setToast({ message: msg, variant: "error" });
    } finally {
      setPipelinePhase("idle");
    }
  }

  function openBatchOptimize() {
    if (!data) return;
    const all = data.items.filter((r) => selectedIds.has(r.image_row_id) && r.optimize_supported);
    if (!all.length) return;
    setBatchQueue(all.map((row) => ({ row, status: "pending" as BatchItemStatus })));
    batchAbortRef.current = false;
    setBatchOpen(true);
  }

  async function runBatchOptimize() {
    setBatchRunning(true);
    batchAbortRef.current = false;
    pauseUntilRef.current = 0;

    const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

    const indexByRowId = new Map<string, number>();
    batchQueue.forEach((entry, idx) => indexByRowId.set(entry.row.image_row_id, idx));

    const productGroups = new Map<string, typeof batchQueue>();
    for (const entry of batchQueue) {
      const key = entry.row.product_shopify_id;
      if (!productGroups.has(key)) productGroups.set(key, []);
      productGroups.get(key)!.push(entry);
    }

    const pool = createPool(BATCH_CONCURRENCY);

    async function processEntry(entry: (typeof batchQueue)[number]) {
      if (batchAbortRef.current) return;

      const idx = indexByRowId.get(entry.row.image_row_id)!;
      const r = entry.row;

      setBatchQueue((prev) =>
        prev.map((e, i) => (i === idx ? { ...e, status: "running" } : e))
      );

      const now = Date.now();
      if (pauseUntilRef.current > now) {
        await sleep(pauseUntilRef.current - now);
      }

      const f = r.flags;
      const applyFn = Boolean(f.weak_filename || f.seo_filename_mismatch);
      const convertWebp = Boolean(f.not_webp);

      try {
        const draft = await postJson("/api/image-seo/product-images/draft", productImageSeoDraftResultSchema, {
          product_shopify_id: r.product_shopify_id,
          image_shopify_id: r.image_shopify_id,
          apply_suggested_filename: applyFn,
          convert_webp: convertWebp,
          auto_vision_alt: true
        });
        if (!draft.ok) {
          setBatchQueue((prev) =>
            prev.map((e, i) => (i === idx ? { ...e, status: "error", message: draft.message } : e))
          );
          return;
        }

        const now2 = Date.now();
        if (pauseUntilRef.current > now2) {
          await sleep(pauseUntilRef.current - now2);
        }

        const opt = await postJson("/api/image-seo/product-images/optimize", productImageSeoOptimizeResultSchema, {
          product_shopify_id: r.product_shopify_id,
          image_shopify_id: r.image_shopify_id,
          apply_suggested_alt: true,
          apply_suggested_filename: applyFn,
          convert_webp: convertWebp,
          alt_override: draft.draft_alt,
          dry_run: false
        });

        setBatchQueue((prev) =>
          prev.map((e, i) =>
            i === idx
              ? { ...e, status: opt.ok ? "success" : "error", message: opt.message }
              : e
          )
        );
      } catch (err) {
        const msg = (err as Error).message;
        if (msg.includes("429") || msg.toLowerCase().includes("throttl")) {
          pauseUntilRef.current = Math.max(pauseUntilRef.current, Date.now() + 2000);
        }
        setBatchQueue((prev) =>
          prev.map((e, i) => (i === idx ? { ...e, status: "error", message: msg } : e))
        );
      }
    }

    const groupTasks = [...productGroups.values()].map((entries) =>
      pool.run(async () => {
        for (const entry of entries) {
          if (batchAbortRef.current) break;
          await processEntry(entry);
        }
      })
    );

    await Promise.all(groupTasks);

    setBatchRunning(false);
    void queryClient.invalidateQueries({ queryKey: ["image-seo-product-images"] });
  }

  function closeBatchModal() {
    if (batchRunning) return;
    setBatchOpen(false);
    setBatchQueue([]);
    setSelectedIds(new Set());
  }

  const batchDone = batchQueue.filter((e) => e.status === "success").length;
  const batchErrors = batchQueue.filter((e) => e.status === "error").length;
  const batchTotal = batchQueue.length;
  const batchFinished = !batchRunning && batchQueue.length > 0 && batchQueue.every((e) => e.status !== "pending" && e.status !== "running");

  const totalPages = data ? Math.max(1, Math.ceil(data.total / pageSize)) : 1;

  const imageModalTitle =
    imageModalPhase === "running"
      ? "Optimizing image"
      : imageModalPhase === "success"
        ? "Optimization complete"
        : imageModalPhase === "error"
          ? "Optimization failed"
          : "Review image";

  return (
    <TooltipProvider delayDuration={250}>
    <div className="w-full min-w-0 space-y-6 p-6 lg:p-8">
      <div className="flex flex-wrap items-center gap-4">
        <div className="flex items-center gap-3">
          <ImageIcon className="h-7 w-7 text-primary" />
          <div>
            <h1 className="text-2xl font-bold text-ink">Image optimization</h1>
            <p className="text-sm text-muted-foreground">
              Shopify-hosted images from products, collections, pages, and blog articles. Run{" "}
              <strong className="font-medium text-ink/80">Shopify</strong> (or <strong className="font-medium text-ink/80">Products</strong>
              ) sync from the left sidebar to refresh the catalog and download product gallery files for local optimization.
              Use <span className="text-ink/80">View</span> to inspect alt text, then{" "}
              <span className="text-ink/80">Optimize</span> to build the draft (including AI alt when configured) and apply to
              Shopify in one run.
            </p>
          </div>
        </div>
      </div>

      {data && (
        <section className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <SummaryCard
            label="Total images"
            value={formatNumber(data.summary.total_images)}
            hint="All Shopify-hosted images across products, collections, pages, and articles."
            tone="border-[#dbe5f3] bg-[linear-gradient(135deg,#ffffff_0%,#eef6ff_100%)]"
          />
          <SummaryCard
            label="Optimized"
            value={formatNumber(data.summary.optimized)}
            hint="Images passing all checks: alt text, SEO filename, and WebP format."
            tone="border-[#d8e9e1] bg-[linear-gradient(135deg,#f8fffb_0%,#e3f7ee_100%)]"
          />
          <SummaryCard
            label="Missing alt"
            value={formatNumber(data.summary.missing_alt)}
            hint="Images with weak or missing alt text — hurts accessibility and SEO."
            tone="border-[#efe2bf] bg-[linear-gradient(135deg,#fffdf5_0%,#fff3cf_100%)]"
          />
          <SummaryCard
            label="Not WebP"
            value={formatNumber(data.summary.not_webp)}
            hint="Images still in JPEG or PNG — converting to WebP improves page speed."
            tone="border-[#f2d9cf] bg-[linear-gradient(135deg,#fff7f4_0%,#ffe7de_100%)]"
          />
        </section>
      )}

      <div className="flex flex-wrap items-center gap-3">
        <div className="relative min-w-[180px] flex-1 max-w-xs">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            id="img-seo-product-q"
            placeholder="Search title or handle…"
            value={productQuery}
            onChange={(e) => {
              setPage(0);
              setProductQuery(e.target.value);
            }}
            className="h-9 pl-8"
          />
        </div>
        <Select
          value={resourceTypeFilter}
          onValueChange={(v) => {
            setPage(0);
            setResourceTypeFilter(v);
          }}
        >
          <SelectTrigger className="h-9 w-[140px]">
            <SelectValue placeholder="All types" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All types</SelectItem>
            <SelectItem value="product">Product</SelectItem>
            <SelectItem value="collection">Collection</SelectItem>
            <SelectItem value="page">Page</SelectItem>
            <SelectItem value="article">Article</SelectItem>
          </SelectContent>
        </Select>
        <div className="inline-flex items-center rounded-lg border border-border bg-muted/30 p-0.5">
          {(
            [
              ["all", "All"],
              ["optimized", "Optimized"],
              ["not_optimized", "Not optimized"],
            ] as const
          ).map(([value, label]) => (
            <button
              key={value}
              type="button"
              onClick={() => {
                setPage(0);
                setStatusFilter(value);
              }}
              className={cn(
                "rounded-md px-3 py-1.5 text-xs font-medium transition-colors",
                statusFilter === value
                  ? "bg-white text-ink shadow-sm dark:bg-card"
                  : "text-muted-foreground hover:text-ink"
              )}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      {isLoading && (
        <div className="flex items-center gap-2 text-muted-foreground">
          <Loader2 className="h-5 w-5 animate-spin" />
          Loading catalog…
        </div>
      )}
      {error && (
        <Card>
          <CardContent className="py-6 text-sm text-destructive">
            {(error as Error).message}
          </CardContent>
        </Card>
      )}

      {data && !isLoading && (
        <>
          <PaginationBar
            page={page}
            totalPages={totalPages}
            pageSize={pageSize}
            total={data.total}
            idSuffix="top"
            onPageChange={setPage}
            onPageSizeChange={(n) => { setPage(0); setPageSize(n); }}
          />

          <div className="rounded-2xl border border-border bg-card">
            <Table className="w-full min-w-[1060px] border-collapse text-left text-sm">
              <TableHeader className="border-b border-border bg-muted/40 text-xs uppercase tracking-wide text-muted-foreground">
                <TableRow>
                  <TableHead className="w-10 px-3 py-3 text-center">
                    <Checkbox
                      className="h-4 w-4"
                      checked={allPageSelected}
                      onCheckedChange={toggleSelectAll}
                      aria-label="Select all on this page"
                    />
                  </TableHead>
                  <TableHead className="px-3 py-3">Preview</TableHead>
                  <ImageSeoSortableTh label="Type" column="type" sortColumn={sortColumn} sortDirection={sortDirection} onSort={toggleSortColumn} />
                  <ImageSeoSortableTh label="Resource" column="title" sortColumn={sortColumn} sortDirection={sortDirection} onSort={toggleSortColumn} />
                  <TableHead className="whitespace-nowrap px-3 py-3">Gallery</TableHead>
                  <TableHead className="whitespace-nowrap px-3 py-3">
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <span className="cursor-help border-b border-dotted border-muted-foreground/60">
                          Local file
                        </span>
                      </TooltipTrigger>
                      <TooltipContent side="top" className="max-w-xs text-left text-xs">
                        After &quot;Sync from Shopify&quot;, product gallery images can be cached on disk (next to your
                        database) for faster Optimize. Rows that are not product gallery media show an em dash.
                      </TooltipContent>
                    </Tooltip>
                  </TableHead>
                  <TableHead className="whitespace-nowrap px-3 py-3">Dimensions</TableHead>
                  <TableHead className="whitespace-nowrap px-3 py-3">Format</TableHead>
                  <TableHead className="whitespace-nowrap px-3 py-3">File size</TableHead>
                  <ImageSeoSortableTh label="Alt" column="alt" sortColumn={sortColumn} sortDirection={sortDirection} onSort={toggleSortColumn} />
                  <ImageSeoSortableTh label="Action" column="optimize" sortColumn={sortColumn} sortDirection={sortDirection} onSort={toggleSortColumn} align="end" />
                  <ImageSeoSortableTh label="Status" column="status" sortColumn={sortColumn} sortDirection={sortDirection} onSort={toggleSortColumn} align="center" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.items.map((row) => (
                  <ImageSeoTableRow
                    key={row.image_row_id}
                    row={row}
                    selected={selectedIds.has(row.image_row_id)}
                    onToggleSelect={toggleRowSelection}
                    onView={openModal}
                  />
                ))}
              </TableBody>
            </Table>
          </div>

          <PaginationBar
            page={page}
            totalPages={totalPages}
            pageSize={pageSize}
            total={data.total}
            idSuffix="bottom"
            onPageChange={setPage}
            onPageSizeChange={(n) => { setPage(0); setPageSize(n); }}
          />
        </>
      )}

      {/* --- Single image optimize modal --- */}
      <Modal
        open={!!modalRow}
        onOpenChange={(o) => {
          if (!o) {
            if (pipelineRunning) return;
            closeImageModal();
          }
        }}
        title={imageModalTitle}
        description={
          modalRow ? `${RESOURCE_TYPE_LABEL[modalRow.resource_type]} · ${modalRow.resource_title}` : undefined
        }
        contentClassName="max-h-[min(92vh,880px)] w-[min(920px,94vw)] overflow-hidden p-4 sm:p-4 [&>div:first-child]:mb-2.5"
      >
        {modalRow &&
        (imageModalPhase === "form" || imageModalPhase === "running" || imageModalPhase === "success") ? (
          <div className="space-y-2">
            <div className="flex flex-col gap-3 md:flex-row md:items-stretch md:gap-4">
              <div className="flex min-w-0 flex-1 flex-col gap-2">
                <ImageSeoOptimizeProgressPanel
                  status={optimizeProgressStatus}
                  pipelinePhase={pipelinePhase === "shopify" ? "shopify" : "draft"}
                  runKey={optimizeRunKey}
                  latestMessage={optimizeProgressLatest}
                  compact
                  className="p-2.5 py-2 text-[11px] leading-snug [&_li]:gap-2"
                />
                {imageModalPhase !== "success" ? (
                  <p className="line-clamp-3 rounded-md border border-dashed border-border bg-muted/10 px-2.5 py-1.5 text-[11px] leading-snug text-muted-foreground">
                    <strong className="font-medium text-ink/90">Optimize</strong> downloads the image, runs vision alt when
                    configured, then saves to Shopify (SEO filename / WebP when your flags require a re-upload).
                  </p>
                ) : null}
              </div>
              <div className="mx-auto flex w-full max-w-[240px] shrink-0 flex-col items-center gap-1 md:mx-0 md:w-60 md:max-w-none">
                {showModalImageComparison ? (
                  <div className="flex w-full justify-between px-0.5 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
                    <span>Before</span>
                    <span>After</span>
                  </div>
                ) : (
                  <p className="w-full text-center text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
                    {imageModalPhase === "running"
                      ? "Optimizing…"
                      : showModalAltOnlySquare
                        ? "Image"
                        : "Before / after"}
                  </p>
                )}
                <div className="aspect-square w-full min-h-0 flex-1 overflow-hidden rounded-lg border border-border bg-muted/20">
                  {showModalImageComparison ? (
                    <ImageComparisonSlider
                      leftImage={modalRow.url}
                      rightImage={modalComparisonNewUrl}
                      altLeft="Product image before optimization"
                      altRight={optimizeDone?.opt.applied_alt?.trim() || "Product image after optimization"}
                      initialPosition={50}
                      className="h-full min-h-0 w-full"
                    />
                  ) : showModalAltOnlySquare ? (
                    <img
                      src={modalRow.url}
                      alt={optimizeDone?.opt.applied_alt?.trim() || ""}
                      className="h-full w-full object-cover"
                    />
                  ) : (
                    <div className="flex h-full w-full flex-col items-center justify-center gap-1 border border-dashed border-border/80 bg-muted/30 px-2 text-center">
                      <ImageIcon className="h-7 w-7 text-muted-foreground/45" aria-hidden />
                      <p className="text-[10px] leading-tight text-muted-foreground">
                        {imageModalPhase === "running"
                          ? "Preview will appear here when the run finishes."
                          : "Run Optimize to load the before / after comparison."}
                      </p>
                    </div>
                  )}
                </div>
              </div>
            </div>

            <ComparisonPanel
              comparisonCurrent={comparisonCurrent}
              comparisonNew={comparisonNew}
              catalogAltAtOpen={catalogAltAtOpen}
              imageModalPhase={imageModalPhase}
            />

            {imageModalPhase === "success" && optimizeDone?.opt.new_image_url ? (
              <p className="text-xs text-slate-500">
                <span className="font-medium text-ink">New media URL:</span>{" "}
                <a
                  href={optimizeDone.opt.new_image_url}
                  className="break-all text-ocean underline"
                  target="_blank"
                  rel="noreferrer"
                >
                  {optimizeDone.opt.new_image_url}
                </a>
              </p>
            ) : null}

            <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border pt-2">
              {imageModalPhase === "success" ? (
                <>
                  <p className="min-w-0 flex-1 text-sm font-medium text-emerald-900 dark:text-emerald-500">
                    Changes are live in Shopify.
                  </p>
                  <Button variant="ocean" type="button" className="shrink-0" onClick={() => closeImageModal()}>
                    Done
                  </Button>
                </>
              ) : (
                <>
                  <Button variant="ghost" type="button" disabled={pipelineRunning} onClick={() => closeImageModal()}>
                    Cancel
                  </Button>
                  <Button
                    variant="ocean"
                    type="button"
                    disabled={pipelineRunning || !modalRow.optimize_supported}
                    onClick={() => void runOptimizePipeline()}
                  >
                    Optimize
                  </Button>
                </>
              )}
            </div>
          </div>
        ) : null}

        {modalRow && imageModalPhase === "error" ? (
          <div className="space-y-4">
            <p className="rounded-xl border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
              {optimizeErrorMsg || "Something went wrong."}
            </p>
            <div className="flex flex-wrap justify-end gap-2">
              <Button
                variant="outline"
                type="button"
                onClick={() => {
                  setImageModalPhase("form");
                  setOptimizeErrorMsg(null);
                }}
              >
                Back
              </Button>
              <Button variant="secondary" type="button" onClick={() => closeImageModal()}>
                Close
              </Button>
            </div>
          </div>
        ) : null}
      </Modal>

      {/* --- Floating batch action bar --- */}
      {selectedIds.size > 0 && (
        <div className="fixed inset-x-0 bottom-6 z-50 mx-auto flex w-fit items-center gap-3 rounded-2xl border border-border bg-card px-5 py-3 shadow-lg">
          <span className="text-sm font-medium text-ink">
            {selectedIds.size} image{selectedIds.size === 1 ? "" : "s"} selected
          </span>
          <Button variant="ocean" size="sm" onClick={openBatchOptimize}>
            Batch Optimize
          </Button>
          <Button
            variant="ghost"
            size="icon"
            className="ml-1 h-7 w-7 rounded-full text-muted-foreground hover:bg-muted/80 hover:text-ink"
            onClick={() => setSelectedIds(new Set())}
            aria-label="Clear selection"
          >
            <X className="h-4 w-4" />
          </Button>
        </div>
      )}

      {/* --- Batch optimize modal --- */}
      <BatchOptimizeModal
        open={batchOpen}
        batchQueue={batchQueue}
        batchRunning={batchRunning}
        batchDone={batchDone}
        batchErrors={batchErrors}
        batchTotal={batchTotal}
        batchFinished={batchFinished}
        onClose={closeBatchModal}
        onStart={() => void runBatchOptimize()}
        onStop={() => { batchAbortRef.current = true; }}
      />

      {toast && (
        <Toast variant={toast.variant} onClose={() => setToast(null)}>
          {toast.message}
        </Toast>
      )}
    </div>
    </TooltipProvider>
  );
}

// ---------------------------------------------------------------------------
// PaginationBar — extracted inline component to avoid duplication
// ---------------------------------------------------------------------------

function PaginationBar({
  page,
  totalPages,
  pageSize,
  total,
  idSuffix,
  onPageChange,
  onPageSizeChange,
}: {
  page: number;
  totalPages: number;
  pageSize: number;
  total: number;
  idSuffix: string;
  onPageChange: (p: number) => void;
  onPageSizeChange: (n: (typeof PAGE_SIZE_OPTIONS)[number]) => void;
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div className="flex flex-wrap items-center gap-4">
        <div className="flex items-center gap-2">
          <Label htmlFor={`img-seo-page-size-${idSuffix}`} className="whitespace-nowrap text-xs text-muted-foreground">
            Per page
          </Label>
          <Select
            value={String(pageSize)}
            onValueChange={(v) => onPageSizeChange(Number(v) as (typeof PAGE_SIZE_OPTIONS)[number])}
          >
            <SelectTrigger id={`img-seo-page-size-${idSuffix}`} className="h-9 w-[88px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {PAGE_SIZE_OPTIONS.map((n) => (
                <SelectItem key={n} value={String(n)}>
                  {n}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <span className="text-sm text-muted-foreground">
          {total} image{total === 1 ? "" : "s"} · Page {page + 1} of {totalPages}
        </span>
      </div>
      <div className="flex items-center gap-2">
        <Button variant="outline" size="sm" disabled={page <= 0} onClick={() => onPageChange(page - 1)}>
          Previous
        </Button>
        <Button
          variant="outline"
          size="sm"
          disabled={page >= totalPages - 1}
          onClick={() => onPageChange(page + 1)}
        >
          Next
        </Button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// ComparisonPanel — the current / new metadata grid inside the optimize modal
// ---------------------------------------------------------------------------

function ComparisonPanel({
  comparisonCurrent,
  comparisonNew,
  catalogAltAtOpen,
  imageModalPhase,
}: {
  comparisonCurrent: { filename: string; format: string; size: string };
  comparisonNew: { filename: string; format: string; size: string; alt: string; reductionPct: number | null };
  catalogAltAtOpen: string;
  imageModalPhase: ImageModalPhase;
}) {
  return (
    <div className="rounded-xl border border-line bg-white p-2 text-sm">
      <div className="grid gap-2 sm:grid-cols-2">
        <div className="rounded-lg border border-line/90 bg-[#f7f9fc] p-2">
          <p className="text-[10px] font-semibold uppercase tracking-wide text-slate-500">Current</p>
          <dl className="mt-1.5 space-y-1.5">
            <div>
              <dt className="text-[10px] font-medium text-slate-500">Filename</dt>
              <dd className="mt-0.5 break-all font-mono text-[11px] leading-snug text-ink">
                {comparisonCurrent.filename}
              </dd>
            </div>
            <div>
              <dt className="text-[10px] font-medium text-slate-500">Format</dt>
              <dd className="mt-0.5 text-[11px] text-slate-700">{comparisonCurrent.format}</dd>
            </div>
            <div>
              <dt className="text-[10px] font-medium text-slate-500">Size</dt>
              <dd className="mt-0.5 font-mono text-[11px] text-slate-600">{comparisonCurrent.size}</dd>
            </div>
            <div>
              <dt className="text-[10px] font-medium text-slate-500">Alt</dt>
              <dd className="mt-0.5 max-h-14 overflow-y-auto whitespace-pre-wrap text-[11px] leading-snug text-ink">
                {catalogAltAtOpen || <span className="italic text-muted-foreground">None</span>}
              </dd>
            </div>
          </dl>
        </div>
        <div className="rounded-lg border border-ocean/25 bg-ocean/[0.06] p-2">
          <p className="text-[10px] font-semibold uppercase tracking-wide text-ocean">New</p>
          <dl className="mt-1.5 space-y-1.5">
            <div>
              <dt className="text-[10px] font-medium text-slate-500">Filename</dt>
              <dd
                className={cn(
                  "mt-0.5 break-all text-[11px] leading-snug",
                  comparisonNew.filename === COMPARISON_NO_CHANGE
                    ? "font-sans italic text-muted-foreground"
                    : "font-mono text-ink"
                )}
              >
                {comparisonNew.filename}
              </dd>
            </div>
            <div>
              <dt className="text-[10px] font-medium text-slate-500">Format</dt>
              <dd
                className={cn(
                  "mt-0.5 text-[11px]",
                  comparisonNew.format === COMPARISON_NO_CHANGE
                    ? "italic text-muted-foreground"
                    : "text-slate-700"
                )}
              >
                {comparisonNew.format}
              </dd>
            </div>
            <div>
              <dt className="text-[10px] font-medium text-slate-500">Size</dt>
              <dd
                className={cn(
                  "mt-0.5 text-[11px]",
                  comparisonNew.size === COMPARISON_NO_CHANGE
                    ? "font-sans italic text-muted-foreground"
                    : "font-mono text-slate-600"
                )}
              >
                <span>{comparisonNew.size}</span>
                {comparisonNew.size !== COMPARISON_NO_CHANGE &&
                comparisonNew.reductionPct != null &&
                comparisonNew.reductionPct > 0 ? (
                  <span className="ml-1.5 font-sans text-emerald-700 dark:text-emerald-400">
                    · {comparisonNew.reductionPct}% smaller
                  </span>
                ) : null}
              </dd>
            </div>
            <div>
              <dt className="text-[10px] font-medium text-slate-500">Alt</dt>
              <dd
                className={cn(
                  "mt-0.5 max-h-14 overflow-y-auto whitespace-pre-wrap text-[11px] leading-snug",
                  comparisonNew.alt === COMPARISON_NO_CHANGE
                    ? "italic text-muted-foreground"
                    : imageModalPhase === "success"
                      ? "text-ink"
                      : "text-slate-500"
                )}
              >
                {comparisonNew.alt}
              </dd>
            </div>
          </dl>
        </div>
      </div>
    </div>
  );
}
