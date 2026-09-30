# App visual modernization: audit and delivery tracker

Updated: September 30, 2026. Baseline: `1bfe6b3`.

## Outcome of the first pass

The Overview and catalog detail pages already establish the intended direction: neutral white surfaces, compact metrics, clear hierarchy, restrained color, and layouts that respond to available width. The rest of the application mixes this with large tinted cards, oversized headings, pill controls, stacked mobile tabs, and several modal styles.

**Inventory: all 24 route patterns. Browser pass: one representative route for every pattern, plus the principal nested tabs and four overlays.** This is an initial audit, not a claim that every function or state has passed. The matrix below preserves the remaining work explicitly.

### Verified problems, in implementation order

| ID | Priority | Finding and evidence | Proposed correction | Source |
|---|---|---|---|---|
| V01 | P1 | Target Keywords expands the document to 650px at a 390px viewport, including Approved, New and Dismissed. [Screenshot](visual-audit-evidence/keywords-target-mobile.png) | Wrap the action group, allow its children to shrink, place secondary actions in a menu where useful. Keep table scrolling inside its container. | `routes/keywords/TargetKeywordsPanel.tsx:562` |
| V02 | P1 | Internal Links expands to 618px at 390px across all six tabs. Its tab row has no wrapping or scrolling boundary. [Screenshot](visual-audit-evidence/internal-links-settings-mobile.png) | Use a shared compact tab bar with contained horizontal scrolling and a visible active tab. | `routes/internal-links-page.tsx:890` |
| V03 | P1 | Article Idea detail renders its main column around 690px wide on a 390px screen; text is cut off. [Screenshot](visual-audit-evidence/idea-detail-mobile.png) | Set grid children to shrink, constrain wide descendants and wrap long content. Inspect the mind map and tables separately. | `routes/idea-detail-page.tsx:712` |
| V04 | P1 | Populated Clusters expands to 436px at 390px. The empty/loading capture did not show this. [Screenshot](visual-audit-evidence/keywords-clusters-loaded-mobile.png) | Reflow populated cluster cards and their action/match rows, then recheck long names. | `routes/keywords/ClustersPanel.tsx` |
| V05 | P1 | Draft new article dialog is 682px tall at 390×600, extending from -41px to 641px, with `overflow-y: visible`. Its top and bottom fall outside the viewport. [Screenshot](visual-audit-evidence/article-draft-dialog-short-mobile.png) | Shared dialog height limit based on dynamic viewport, scrollable body, reachable close and submit controls. Check keyboard-open and short landscape screens. | `components/ui/modal.tsx`, `components/ui/dialog.tsx`, `routes/articles-page.tsx` |
| V06 | P1 | Blog-specific article rows link to `/blogs`, not the article detail route. Confirmed in rendered links and source. | Build each link from its blog and article handles; verify click and keyboard navigation. | `routes/blog-articles-page.tsx:115` |
| V07 | P2 | Catalog and image pages stack large, explanatory KPI cards on mobile. The table is pushed well below the first screen. [Collections example](visual-audit-evidence/collections-mobile.png) | Compact summaries with short labels, 24–30px values and optional help; use two columns only where labels and values fit. | Product/content/article lists, image optimization, embeddings |
| V08 | P2 | Keyword Research and Settings stack four large tabs, then repeat the selected tab's description in a separate panel. [Settings example](visual-audit-evidence/settings-mobile.png) | Compact shared navigation; one contextual explanation; provider and task controls higher on the screen. | `routes/keywords-page.tsx:141`, `routes/settings-page.tsx:521` |
| V09 | P2 | Shared controls do not match the Overview: mixed fonts, strongly rounded or square dialogs, gradient cards, faint boundaries, inconsistent blue/purple/black action emphasis. [Ranking dialog example](visual-audit-evidence/ranking-add-dialog-mobile.png) | Shared tokens and components, applied deliberately by page family. | `styles.css`, `components/ui/{card,button,badge,tabs,dialog,modal}.tsx`, `routes/overview.css` |
| V10 | P2 | Image review opens with setup/pipeline explanations and an empty comparison placeholder; current image details and actions require scrolling on mobile. | Put image identity and comparison first, compact progress, disclose technical detail, keep the current action reachable. | `routes/image-seo-page/BatchOptimizeModal.tsx` |
| V11 | P2 | Several dense tables require substantial horizontal scrolling. Rankings is approximately 1264px wide; Opportunities 1095px; Article Ideas 1090px. The document itself stays contained in these captures. | Preserve information and existing full-catalog behavior; provide clear scroll affordance, predictable identity column and prioritized visible fields. | Shared DataTable and page-specific tables |
| V12 | P2 | Loading summaries briefly present zeros and generic “Loading…”; Overview's unavailable Google state exposes a raw token endpoint error. | Distinguish unknown/loading from zero. Use concise recovery guidance and put technical diagnostics behind disclosure. | Router fallback, catalog summaries, Overview error panels |
| V13 | Investigate | Ads Historical metrics measured 395px document width at 390px; the other Ads tabs were contained. | Reproduce after the shared controls change and isolate the small overflow before calling it fixed. | `routes/google-ads-lab-page.tsx` |

P1 means broken access, clipping, or navigation; P2 means hierarchy, consistency, or density. Source paths above are relative to `frontend/src/`.

## Design contract

Use the existing Overview as the reference, including its restrained hierarchy. Do not restyle by adding broad selectors that silently override unrelated screens.

- **Surfaces:** white panels, subtle `#e2e7ed` borders, 14px default radius, minimal shadow. Color highlights status or selection; it does not decorate every metric.
- **Typography:** one application font stack, consistent heading levels. Reference values: page title 32px, section title 18px, body 14px, supporting text 12–13px. Compact mobile headings when needed. Tabular numerals for metrics.
- **Spacing:** a shared 4/8/12/16/24/32px scale. Default card padding 16–20px. Avoid repeated 28–30px rounding and large padding on every nested panel.
- **Metrics:** label, value, one concise supporting line. Keep values and statuses readable as whole units; never force words into narrow letter stacks. Use available-width grids.
- **Actions:** one visually primary action per task area. Secondary actions use a neutral treatment; destructive actions remain explicit. Preserve action names and pending/disabled behavior.
- **Navigation:** compact tab bars, consistent selected/focus states, contained scrolling on narrow screens. Mobile navigation should not consume most of the first screen.
- **Forms:** consistent label/help/error spacing, practical input heights, visible focus, clear required fields. Advanced configuration stays available through disclosure.
- **Overlays:** consistent appearance, max height with a scrollable body, reachable close/action controls, focus trap, Escape and restored focus. Account for the mobile keyboard and safe areas.
- **Tables:** preserve data, sorting, selection and existing performance invariants. Contain horizontal scrolling; never hide overflow at the page root as a substitute for fixing layout.
- **States:** matching loading, empty, error, running and success treatments. “No data” is different from zero. Technical diagnostics should not dominate the normal flow.
- **Accessibility:** semantic controls and heading order, keyboard access, visible focus, non-color status cues, adequate contrast and touch targets. These require explicit verification, not appearance alone.

## Route coverage and rollout matrix

All routes below are relative to `/app`. **I** = inventoried in source; **A** = initial browser audit captured; **U/V/F** = updated / visually verified / functionally verified during modernization. A does not imply all states tested. All U/V/F cells are pending at this baseline.

| Route | Surfaces included in initial pass | Remaining states and functions to verify | Batch | I/A | U/V/F |
|---|---|---|---|---|---|
| `/` | Overview, unavailable Google panels, indexing and catalog sections | Loaded GSC/GA4 charts, all scopes/periods, coverage expansion, attention links, narrow layouts | Reference + final regression | ✓/✓ | —/—/— |
| `/products` | Populated catalog, metrics, desktop/mobile | Filters, sort directions, long titles, segments/content controls, empty/error | 2 | ✓/✓ | —/—/— |
| `/products/:handle` | Representative product signals/editor | Image gallery, preview, field validation, regeneration, save, opportunity task, Sidekick, query/segment/link panels | 2 | ✓/✓ | —/—/— |
| `/collections` | Populated list, metrics | Filters, sorts, bulk controls, empty/error | 2 | ✓/✓ | —/—/— |
| `/collections/:handle` | Representative collection editor/signals | Same detail states; missing/present image, related items, unsaved changes | 2 | ✓/✓ | —/—/— |
| `/pages` | Populated list, metrics | Filters, sorts, bulk controls, empty/error | 2 | ✓/✓ | —/—/— |
| `/pages/:handle` | Representative page editor/signals | Same detail states; empty/long body and SEO fields | 2 | ✓/✓ | —/—/— |
| `/blogs` | Populated blog list | Sort/search, empty/error, blog navigation | 2 | ✓/✓ | —/—/— |
| `/blogs/:blogHandle` | Populated article list, incorrect links confirmed | Correct article navigation, filter/sort, empty/error | 2 | ✓/✓ | —/—/— |
| `/articles` | Populated list; Draft new article dialog at desktop/mobile and short mobile | Draft validation, slug reset, generating/resume/failure/success, list filtering | 2 + 4 | ✓/✓ | —/—/— |
| `/articles/:blogHandle/:articleHandle` | Representative article editor/signals | Preview, metadata/body edits, draft/publish behavior, image tools, Sidekick, save states | 2 + 4 | ✓/✓ | —/—/— |
| `/rankings` | Populated table, Add keyword dialog; one 768px tablet capture | Edit/remove/restore, history, group/sort/search, cost confirmation, running/stop, errors and budget limits | 3 | ✓/✓ | —/—/— |
| `/keywords` | Seeds, Competitors, Targets, Clusters; Approved/New/Dismissed states for competitors and targets | Seed edits, add/discovery dialogs, filters/sorts/selections, match change, long clusters, progress/cancel/error | 1 pilot | ✓/✓ | —/—/— |
| `/keywords/clusters/:id` | Populated representative cluster | Coverage dialog, assigned pages, keyword actions, empty/error and long content | 3 | ✓/✓ | —/—/— |
| `/keywords/competitors/:domain` | Populated representative competitor | Metric refresh/progress, filters/sorts/paging, drill-down, empty/error | 3 | ✓/✓ | —/—/— |
| `/article-ideas` | Approved/New/Rejected queues | Filters, selection/bulk actions, status changes, delete confirmation, generation and failures | 4 | ✓/✓ | —/—/— |
| `/article-ideas/:ideaId` | Populated brief, strategy/mind-map content, clipping | Draft dialog, links/coverage disclosure, generation/resume, linked articles, editable fields and errors | 4 | ✓/✓ | —/—/— |
| `/opportunities` | Populated inbox, task controls | Filters/sorts/paging; fixture-based prepare, draft compare/load, applied/monitoring/dismiss/reopen, empty/error | 3 | ✓/✓ | —/—/— |
| `/google-ads-lab` | Keyword ideas, Historical metrics, Forecast metrics, Ad group themes | Field validation/reset, result/error/loading views using saved fixtures; minor overflow reproduction | 5 | ✓/✓ | —/—/— |
| `/embeddings` | Populated metrics and coverage table | Refresh/progress, unavailable key, partial/error/empty coverage | 5 | ✓/✓ | —/—/— |
| `/image-seo` | Populated table and Review image dialog | Type/status filters, search/sort/paging, selection/batch, gallery; optimize progress/comparison/failure/success fixtures | 5 | ✓/✓ | —/—/— |
| `/api-usage` | Summary and low/empty recent usage | All time ranges, populated providers/charts, errors/empty, narrow tables | 6 | ✓/✓ | —/—/— |
| `/internal-links` | Suggestions, Applied, Orphans, Graph Stats, Outcomes, Settings | Graph Map mode; populated suggestions/applied; preview/apply/undo/reconcile, source settings, failure/conflicts | 5 | ✓/✓ | —/—/— |
| `/settings` | Integrations, AI Models, Runtime, Data Sources | Provider reveal/select/test/save, validation, dirty states, OAuth/selectors, errors/success and keyboard traversal | 6 | ✓/✓ | —/—/— |

### Shared surfaces

| Surface | Observed in this pass | Remaining verification |
|---|---|---|
| App shell | Desktop sidebar and compact mobile header across routes | Expanded/collapsed sidebar; mobile navigation open/close, route change, scroll and focus |
| Bottom sync bar and dialog | Idle dialog desktop/mobile; Escape dismissal and focus return to trigger confirmed | Running/partial/error/complete, service settings and logs, long output, keyboard and safe area |
| Sidekick | Entry points inventoried from detail routes and shared provider | Open/close, responsive sheet, composer, long conversation, pending/tool/error states, focus return |
| Shared overlays | Image review, ranking add, article draft, sync idle opened | Every remaining dialog/menu/popover; keyboard-open height, zoom, long errors, focus order and dismissal |
| Shared tables | Populated examples; contained versus document overflow distinguished | All row/column menus, sorting, filtering, selection, focus, empty/error/loading and large datasets |
| Notifications | Notification region present | Success/error announcements, long messages, overlay stacking, dismissal and reduced motion |

## Execution batches

1. **Shared foundations + Keyword Research pilot.** Add shared tokens and compact metric/tab/action/dialog patterns. Migrate all four keyword tabs and their nested review queues. Fix V01/V04; verify the dialog constraint with Article draft and Ranking add. Make targeted containment fixes for V02/V03 while retaining each workflow. Check the Overview and detail references for regressions.
2. **Catalog family.** Products, Collections, Pages, Blogs, blog articles, all Articles and their details. Compact list summaries, consistent table tools, detail forms and actions. Fix V06. Preserve the detail improvements already shipped.
3. **Research and opportunities.** Rankings, cluster and competitor details, Opportunity Inbox and the draft review/task flow. Consistent progress, costs, statuses and action hierarchy.
4. **Content planning.** Article Ideas queues/detail, drafting dialogs, outline/mind-map and coverage displays, generation/resume states.
5. **Optimization and tools.** Internal Links, Image Optimization, Embeddings and Ads lab. Align forms, tables, job progress and review overlays.
6. **Settings and usage.** Settings tabs/provider forms, API Usage, then global shell/Sidekick/notification polish and full regression sweep.

Each batch must update this tracker and include before/after evidence. A batch is not complete merely because the default page looks better.

## Completion gate for each surface

- [ ] Page and nested tabs match the design contract; the primary task is apparent.
- [ ] 1440px desktop, 1024px compact desktop, 768px tablet, 390px and 320px phone checks.
- [ ] Short-height screen and 200% zoom; form/overlay actions remain reachable.
- [ ] Long text, populated and empty datasets, loading, error, running and success states.
- [ ] No unintended document overflow; deliberate table/map scrolling remains usable.
- [ ] Menus/dialogs/sheets and expanded content tested; no obstruction by sync bar or Sidekick.
- [ ] Keyboard navigation, focus visibility/order/trap/return, labels and status announcements.
- [ ] Existing filter/sort/paging/selection, form validation and workflow transitions preserved.
- [ ] Appropriate component/regression tests pass; no new API payload differences if read paths change.
- [ ] Clean production rebuild and local port-8000 verification after application changes; commit and push.

## Method, evidence and limitations

- Cross-checked `frontend/src/app/router.tsx`, the Screens / Pages section of `TECHNICAL_DOC.md`, shared components and route sources.
- Browser pass used the local built SPA at `http://127.0.0.1:8000/app/`, with 1440×1000 and 390×844 captures. Added a 390×600 draft-dialog check and one 768×1024 Rankings check. **Tablet, 320px and zoom coverage are not complete.**
- [Machine-readable observations](visual-audit-observations.json) record document overflow per captured view. These are browser DOM measurements, not an automated accessibility or functional test suite.
- Screenshot evidence in this directory is a selected set of confirmed issues. Other session captures were stored temporarily under `/tmp/shopifyseo-visual-audit`; that temporary directory is not a durable deliverable.
- Initial products, images, embeddings, clusters and competitor captures were sometimes loading; the `*-loaded` captures supersede them. The first `cluster-detail` capture retained the preceding screen during navigation; use `cluster-detail-loaded`. An observation alone does not certify all below-the-fold content.
- Local catalog data is a stored snapshot; Google connections currently produce unavailable states. This pass does not establish current remote-production behavior or populated Google-chart appearance.
- No Shopify saves, paid generation/ranking requests, optimizer runs, sync jobs, credential changes or internal-link writes were triggered by the audit. Their transitions need deterministic fixtures or an explicitly scoped integration check during the relevant batch.
- Application code is unchanged in this first pass. The deliverables are this inventory, prioritized findings, evidence and implementation plan.
- Preserve `TECHNICAL_DOC.md` Performance Invariants during implementation: narrow catalog reads, object-specific context, indexed keyword lookups, scoped GSC trends, existing client sorting and full list behavior. UI polish is not authorization to change data semantics or remove safeguards.
