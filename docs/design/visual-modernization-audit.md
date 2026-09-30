# App visual modernization: audit and delivery tracker

Updated: September 30, 2026. Baseline: `1bfe6b3`.

## Batch 1 implementation

Shared foundations and the Keyword Research pilot are implemented. The original audit findings below remain as the baseline, with this section recording progress.

- Neutral 14px shared cards, compact button corners, and an Overview-aligned application font stack.
- Scrollable research tabs keep the active tab visible on selection and resize; the repeated description panel is removed.
- Seeds use neutral keyword chips with accessible remove labels. Target actions wrap and their introduction is shorter. Competitor controls and review tabs remain available at narrow widths. Cluster headers, matching controls and statistics wrap; keyboard activation of Change no longer triggers the enclosing card's navigation.
- Shared dialogs have viewport-bounded internal scrolling. The article draft dialog at 390×600 now measures 568px high, with 16px clearance above and below.
- Targeted containment fixes cover Internal Links navigation and the Article Idea main/sidebar grid. Long product tags also wrap at 320px.

### Verification

- Clean production build and local FastAPI restart completed after frontend edits.
- **67 frontend tests passed** using `npm test -- --maxWorkers=1 --testTimeout=15000`, including a new test for keyboard selection and keeping the active tab visible. Initial concurrent runs timed out under machine load; the final serial run retained the table's original 4-second sort assertion and measured 984ms.
- Browser width checks covered 1440, 1024, 768, 390 and 320px for all four research tabs; nested competitor and target review queues were also checked. Target/cluster page overflow from the original audit is resolved in the checked populated states.
- Seed source filtering, target search with an empty result, cluster Change via Enter, short-screen draft dialog scrolling/Escape, and sync dialog open/Escape were exercised without saving data or running automation.
- Overview and a populated product detail were included in regression checks. The idea-detail grid and Internal Links navigation were checked at all five widths.
- Remaining acceptance work: physical mobile keyboard, 200% browser zoom, exhaustive focus-return/contrast review, paid/running/error mutation fixtures and the rest of the per-route functional matrix. These are still open; batch implementation is not full application certification.

Evidence: [desktop pilot](batch-1-evidence/keywords-desktop.png), [mobile targets](batch-1-evidence/targets-mobile.png), [short-screen dialog](batch-1-evidence/draft-dialog-mobile.png), [idea detail](batch-1-evidence/idea-mobile.png), [width measurements](batch-1-evidence/width-checks.json). Width measurements include intermediate failing observations followed by explicitly named corrected checks.

Catalog implementation is recorded in Batch 2 below.

## Batch 2 implementation

The catalog family now follows the Overview reference: Products, Collections, Pages, Blogs, blog articles, all Articles and their detail editors.

- Compact neutral summaries use four columns when space permits and two on phones; help text stays available through keyboard/touch popovers. Loading and unavailable values display an em dash instead of a misleading zero.
- Consistent headings, rectangular search fields, wrapping action rows and restrained table panels.
- Table cell text is 13px (previous title links were 8.67px), names wrap to two lines, and the identity column remains visible while scrolling. The scroll region is keyboard-focusable and active sorting is announced. All rows remain rendered and memoized.
- Detail editors have clear page titles, separated action headers, consistent labels, fields and focus indicators. Phone metric grids use two columns; image/editor columns still stack by available width.
- **V06 fixed:** blog-specific article links open the matching detail route. A regression test includes an encoded article handle.
- Gallery checks found and corrected a Sidekick stacking issue and missing focus return. Sidekick stays below overlays; controlled modals return focus to their opener.

### Verification

- Production rebuild and local port-8000 restart completed after application changes.
- **69 frontend tests passed:** the full existing suite (68) plus the new controlled-modal focus regression (1), run with one worker and a 15-second per-test timeout. The final full-suite table sort measured 1160ms against the unchanged 4-second assertion.
- After the final build, gallery Escape restored focus to Open image preview; Sidekick Escape restored focus to Open Sidekick. The gallery controls were visually confirmed unobstructed by the floating Sidekick button.
- All six list patterns and four detail patterns checked at 1440, 1024, 768, 390 and 320px with populated local data: document width equals viewport width. Wide catalog tables scroll inside their own region.
- Product empty search and sorting, summary help/Escape, table keyboard scrolling with a stationary identity column, blog-to-list navigation and Enter-to-article navigation were exercised. Product gallery navigation and containment were checked at 320×600; collection fields were inspected at the same small size.
- The initial before/after implementation JSON comparisons for `/api/products`, `/api/collections`, `/api/pages`, `/api/articles` and `/api/blogs` were equal. During final verification the local catalog data changed (products increased from 830 to 884), so final payloads differ from that initial capture. Final response/row keys remain equal; [comparison metadata](batch-2-evidence/api-contract-checks.json) records the counts and limitation. No backend/read-path changes were made, and this batch did not trigger a sync. Screenshots span these two data states.
- Remaining acceptance work: Shopify save/regeneration/AI and failure/success transitions with deterministic fixtures; complete keyboard/contrast review; physical mobile keyboard and 200% browser zoom. Collection gallery with a present image remains untested in this local snapshot. Sidekick conversation/tool states remain in Batch 6.

Evidence: [desktop products](batch-2-evidence/products-desktop.png), [mobile products](batch-2-evidence/products-mobile.png), [mobile collections](batch-2-evidence/collections-mobile.png), [desktop detail](batch-2-evidence/collection-detail-desktop.png), [small-phone editor](batch-2-evidence/collection-editor-small-phone.png), [mobile article](batch-2-evidence/article-detail-mobile.png), [gallery](batch-2-evidence/gallery-small-phone.png), [width measurements](batch-2-evidence/width-checks.json). Original audit screenshots above/below provide the before state.

Research implementation is recorded in Batch 3 below.

## Batch 3 implementation

Rankings, cluster/competitor details and Opportunity Inbox now share compact summaries, consistent headings, neutral panels and named keyboard-scrollable tables. Identity columns are bounded to 200px so they cannot cover adjacent controls on narrow screens. Competitor secondary research metrics use a native disclosure. Opportunity task evidence, status and draft comparisons have a clearer hierarchy and wrap by available width.

- Fixed Inbox pagination: Previous remains available on the last page. A regression test navigates forward and back with mocked responses.
- Added accessible labels to competitor navigation and the Inbox type filter; sort state is on table headers. Cluster keyword disclosure announces its expanded state.
- All **70 frontend tests passed**; the changed pagination test also passed separately. Clean production rebuild/restart completed. A test-only TypeScript option mismatch was corrected before the final build.
- Populated Rankings, cluster details, competitor details and Inbox checked at 1440, 1024, 768, 390 and 320px with no document overflow.
- Verified ranking empty search; Add keyword dialog at 320×600; cluster keyword collapse and coverage dialog via click/Enter; competitor metric disclosure via Enter; Inbox filter, empty result, last-page Next disabled and Previous return. Coverage dialog measured 16px top/bottom clearance and internal scrolling at 320×600.
- Existing mocked task tests still verify explicit draft loading, edited-field review, reviewed snapshot restoration and retry errors. No ranking checks, task preparation, Shopify saves or paid generation were run live. Populated task review screenshots, every mutation state, full keyboard focus review and zoom/physical-keyboard checks remain open. No backend or API query construction changed; pagination changes only expose the existing Previous action.

Evidence: [Rankings mobile](batch-3-evidence/rankings-mobile.png), [ranking dialog](batch-3-evidence/rankings-dialog-small.png), [cluster mobile](batch-3-evidence/cluster-mobile.png), [coverage dialog](batch-3-evidence/coverage-small-phone.png), [competitor mobile](batch-3-evidence/competitor-mobile.png), [Inbox desktop](batch-3-evidence/inbox-desktop.png), [Inbox mobile](batch-3-evidence/inbox-mobile.png), [width checks](batch-3-evidence/width-checks.json).

## Batch 4 — content planning implemented

Article Ideas now shares compact status tabs, a named scrollable table with sticky title/selection columns, keyboard sort controls and native detail links. Detail headings/actions wrap by available width, the brief/sidebar grid stacks, and research explanations are shorter without removing refresh/cost guidance.

- Existing 70 frontend tests passed; a new keyboard regression test passed separately (71 total), covering status-tab navigation, sorting and opening a detail without selecting its row. Clean production build/restart passed.
- Populated queue and idea detail checked at 1440, 1024, 768, 390 and 320px: no document overflow. New status tab, title sort and detail link activated with the keyboard.
- Draft dialog at 320×600 stays between y=16 and y=584 with internal scrolling; opening and cancelling verified. Generation, live status edits/deletion, SERP refresh and Shopify writes were not triggered. Success/error/resume fixtures and zoom remain open.
- No API request or response handling changed. Existing full-row sorting/selection is retained.

Evidence: [idea detail](batch-4-evidence/idea-desktop.png), [queue](batch-4-evidence/queue-desktop.png), [short draft dialog](batch-4-evidence/draft-small-phone.png), [width checks](batch-4-evidence/width-checks.json).

## Batch 5 — optimization and tools implemented

Image Optimization, Embeddings, Internal Links and Google Ads lab use compact headings, neutral summaries, named table scroll regions and contained panels. Internal Links now uses shared keyboard tabs. Ads setup and reference notes are disclosures; request/response editors stack based on available width. Image review shows the current image immediately, discloses idle pipeline steps and scrolls within the viewport.

- All 71 frontend tests passed. Final clean production rebuild/restart passed after correcting narrow image-type badges, embedding labels and long Ads API-name wrapping.
- Populated image catalog, embedding coverage and Internal Links suggestions checked at 1440/1024/768/390/320px with no document overflow. All four Ads tabs checked at those widths after the wrapping fix; expanded Ads notes also fit at 320px.
- All six Internal Links tabs opened at 320px. Orphans is populated; Suggestions/Applied/Graph/Outcomes have empty data. Graph Map toggle works in the empty state. Existing preview/confirm/guardrail tests pass with mocked data.
- Image review opened at 320×600: y=24 to y=576 with internal scrolling and visible current image. Closing returns to the catalog. No optimize/rebuild/embedding refresh, Ads request, save or paid run was triggered. Populated graph/suggestion states, optimization progress/results and external failures remain fixture/integration gaps.
- API request construction and data handlers unchanged.

Evidence: [image catalog](batch-5-evidence/images-desktop.png), [review dialog](batch-5-evidence/image-review-mobile.png), [embeddings](batch-5-evidence/embeddings-mobile.png), [links settings](batch-5-evidence/links-settings-mobile.png), [Ads lab](batch-5-evidence/ads-mobile.png), [width checks](batch-5-evidence/width-checks.json).

## Batch 6 — Settings, usage and shared interactions implemented

Settings has compact shared tabs, smaller provider panels and form grids that shrink correctly around long model/property names. API Usage uses compact neutral cost cards, contained tables, accessible period selection and keyboard-readable daily bars. Shared dialogs restore opener focus and respect explicit caller focus overrides. Mobile navigation supports Escape and focus return; the shell has a skip link. Toasts are placed above the sync bar and shared animation respects reduced-motion preferences. The question map has a named keyboard-scrollable region. Overview connection failures lead with recovery guidance and disclose diagnostics.

- **73 frontend tests passed**, including two new controlled-dialog focus regressions and the existing Settings save/provider/secret-field tests with mocked services. Sort performance passed at 1.53 seconds for 830 rows. A first run under heavy machine load timed out; the separate full rerun passed. An initial navigation ref typing error was corrected before the successful build.
- All four Settings sections checked at 1440/1024/768/390/320px. AI Models and Data Sources initially overflowed at 320px because nested field grids kept their intrinsic width; the final CSS fixes were verified at all five widths.
- API Usage 7/30/90-day periods checked at all five widths with no document overflow. Period controls update their selected state. Recent provider tables are populated; recent daily charts have sparse/empty data.
- Final clean production rebuild/restart passed. Overview and the populated Keyword Research Clusters view remained contained at 320px; the skip link focuses main content. The Overview diagnostic disclosure is compiled but its unavailable-provider branch was not present in this final live snapshot.
- Browser checks confirm Escape closes mobile navigation and focuses its button; sync panel closes and focuses its bar; controlled Ranking Add dialog closes and focuses Add keyword. Existing Modal focus test also passes after moving the behavior into shared DialogContent.
- Settings credentials were kept masked. No credentials were changed, provider tests invoked, settings saved, or paid requests made in the live app. Connection/save behavior remains covered by mocked tests, not a new external integration certification.

Evidence: [Settings desktop](batch-6-evidence/settings-desktop.png), [Settings mobile](batch-6-evidence/settings-mobile.png), [API Usage mobile](batch-6-evidence/usage-mobile.png), [width checks](batch-6-evidence/width-checks.json).

### Rollout checkpoint

All six implementation batches have now been applied. The route matrix deliberately retains **partial visual/functional verification**: physical mobile keyboards, 200% browser zoom, exhaustive focus/contrast checks, populated live Google data, paid job progress/results and live Shopify writes are not certified by these checks. The remaining-state column is the follow-up test backlog; it should not be mistaken for a list of screens left unstyled. No backend read paths or API response schemas changed in batches 3–6.


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

All routes below are relative to `/app`. **I** = inventoried in source; **A** = initial browser audit captured; **U/V/F** = updated / visually verified / functionally verified during modernization. A does not imply all states tested. U/V/F below describe the full per-route gate, so partial batch checks do not imply exhaustive verification.

| Route | Surfaces included in initial pass | Remaining states and functions to verify | Batch | I/A | U/V/F |
|---|---|---|---|---|---|
| `/` | Overview, unavailable Google panels, indexing and catalog sections | Loaded GSC/GA4 charts, all scopes/periods, coverage expansion, attention links, narrow layouts | Reference + final regression | ✓/✓ | —/—/— |
| `/products` | Populated catalog, metrics, desktop/mobile | Filters, sort directions, long titles, segments/content controls, empty/error | 2 | ✓/✓ | ✓/partial/partial |
| `/products/:handle` | Representative product signals/editor | Image gallery, preview, field validation, regeneration, save, opportunity task, Sidekick, query/segment/link panels | 2 | ✓/✓ | ✓/partial/partial |
| `/collections` | Populated list, metrics | Filters, sorts, bulk controls, empty/error | 2 | ✓/✓ | ✓/partial/partial |
| `/collections/:handle` | Representative collection editor/signals | Same detail states; missing/present image, related items, unsaved changes | 2 | ✓/✓ | ✓/partial/partial |
| `/pages` | Populated list, metrics | Filters, sorts, bulk controls, empty/error | 2 | ✓/✓ | ✓/partial/partial |
| `/pages/:handle` | Representative page editor/signals | Same detail states; empty/long body and SEO fields | 2 | ✓/✓ | ✓/partial/partial |
| `/blogs` | Populated blog list | Sort/search, empty/error, blog navigation | 2 | ✓/✓ | ✓/partial/partial |
| `/blogs/:blogHandle` | Populated article list, incorrect links confirmed | Correct article navigation, filter/sort, empty/error | 2 | ✓/✓ | ✓/partial/partial |
| `/articles` | Populated list; Draft new article dialog at desktop/mobile and short mobile | Draft validation, slug reset, generating/resume/failure/success, list filtering | 2 + 4 | ✓/✓ | ✓/partial/partial |
| `/articles/:blogHandle/:articleHandle` | Representative article editor/signals | Preview, metadata/body edits, draft/publish behavior, image tools, Sidekick, save states | 2 + 4 | ✓/✓ | ✓/partial/partial |
| `/rankings` | Populated table, Add keyword dialog; one 768px tablet capture | Edit/remove/restore, history, group/sort/search, cost confirmation, running/stop, errors and budget limits | 3 | ✓/✓ | ✓/partial/partial |
| `/keywords` | Seeds, Competitors, Targets, Clusters; Approved/New/Dismissed states for competitors and targets | Seed edits, add/discovery dialogs, filters/sorts/selections, match change, long clusters, progress/cancel/error | 1 pilot | ✓/✓ | ✓/partial/partial |
| `/keywords/clusters/:id` | Populated representative cluster | Coverage dialog, assigned pages, keyword actions, empty/error and long content | 3 | ✓/✓ | ✓/partial/partial |
| `/keywords/competitors/:domain` | Populated representative competitor | Metric refresh/progress, filters/sorts/paging, drill-down, empty/error | 3 | ✓/✓ | ✓/partial/partial |
| `/article-ideas` | Approved/New/Rejected queues | Filters, selection/bulk actions, status changes, delete confirmation, generation and failures | 4 | ✓/✓ | ✓/partial/partial |
| `/article-ideas/:ideaId` | Populated brief, strategy/mind-map content, clipping | Draft dialog, links/coverage disclosure, generation/resume, linked articles, editable fields and errors | 4 | ✓/✓ | ✓/partial/partial |
| `/opportunities` | Populated inbox, task controls | Filters/sorts/paging; fixture-based prepare, draft compare/load, applied/monitoring/dismiss/reopen, empty/error | 3 | ✓/✓ | ✓/partial/partial |
| `/google-ads-lab` | Keyword ideas, Historical metrics, Forecast metrics, Ad group themes | Field validation/reset, result/error/loading views using saved fixtures; minor overflow reproduction | 5 | ✓/✓ | ✓/partial/partial |
| `/embeddings` | Populated metrics and coverage table | Refresh/progress, unavailable key, partial/error/empty coverage | 5 | ✓/✓ | ✓/partial/partial |
| `/image-seo` | Populated table and Review image dialog | Type/status filters, search/sort/paging, selection/batch, gallery; optimize progress/comparison/failure/success fixtures | 5 | ✓/✓ | ✓/partial/partial |
| `/api-usage` | Summary and low/empty recent usage | All time ranges, populated providers/charts, errors/empty, narrow tables | 6 | ✓/✓ | ✓/partial/partial |
| `/internal-links` | Suggestions, Applied, Orphans, Graph Stats, Outcomes, Settings | Graph Map mode; populated suggestions/applied; preview/apply/undo/reconcile, source settings, failure/conflicts | 5 | ✓/✓ | ✓/partial/partial |
| `/settings` | Integrations, AI Models, Runtime, Data Sources | Provider reveal/select/test/save, validation, dirty states, OAuth/selectors, errors/success and keyboard traversal | 6 | ✓/✓ | ✓/partial/partial |

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
- The initial audit changed no application code. The batch sections above record subsequent implementation and verification.
- Preserve `TECHNICAL_DOC.md` Performance Invariants during implementation: narrow catalog reads, object-specific context, indexed keyword lookups, scoped GSC trends, existing client sorting and full list behavior. UI polish is not authorization to change data semantics or remove safeguards.
