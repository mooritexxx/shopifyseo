import { Suspense, lazy } from "react";
import type { ReactNode } from "react";
import { createBrowserRouter } from "react-router-dom";

import { AppShell } from "../components/shell/app-shell";

const OverviewPage = lazy(() => import("../routes/overview-page").then((module) => ({ default: module.OverviewPage })));
const ProductsPage = lazy(() => import("../routes/products-page").then((module) => ({ default: module.ProductsPage })));
const ProductDetailPage = lazy(() => import("../routes/product-detail-page").then((module) => ({ default: module.ProductDetailPage })));
const ContentListPage = lazy(() => import("../routes/content-list-page").then((module) => ({ default: module.ContentListPage })));
const ContentDetailPage = lazy(() => import("../routes/content-detail-page").then((module) => ({ default: module.ContentDetailPage })));
const SettingsPage = lazy(() => import("../routes/settings-page").then((module) => ({ default: module.SettingsPage })));
const BlogsPage = lazy(() => import("../routes/blogs-page").then((module) => ({ default: module.BlogsPage })));
const BlogArticlesPage = lazy(() => import("../routes/blog-articles-page").then((module) => ({ default: module.BlogArticlesPage })));
const ArticlesPage = lazy(() => import("../routes/articles-page").then((module) => ({ default: module.ArticlesPage })));
const ArticleDetailPage = lazy(() => import("../routes/article-detail-page").then((module) => ({ default: module.ArticleDetailPage })));
const KeywordsPage = lazy(() => import("../routes/keywords-page").then((module) => ({ default: module.KeywordsPage })));
const ClusterDetailPage = lazy(() => import("../routes/cluster-detail-page").then((module) => ({ default: module.ClusterDetailPage })));
const ArticleIdeasPage = lazy(() => import("../routes/article-ideas-page").then((module) => ({ default: module.ArticleIdeasPage })));
const IdeaDetailPage = lazy(() => import("../routes/idea-detail-page").then((module) => ({ default: module.IdeaDetailPage })));
const CompetitorDetailPage = lazy(() => import("../routes/competitor-detail-page").then((module) => ({ default: module.CompetitorDetailPage })));
const EmbeddingsPage = lazy(() => import("../routes/embeddings-page"));
const ImageSeoPage = lazy(() =>
  import("../routes/image-seo-page").then((m) => ({ default: m.ImageSeoPage }))
);
const ApiUsagePage = lazy(() =>
  import("../routes/api-usage-page").then((m) => ({ default: m.ApiUsagePage }))
);
const GoogleAdsLabPage = lazy(() =>
  import("../routes/google-ads-lab-page").then((m) => ({ default: m.GoogleAdsLabPage }))
);

function PageFallback() {
  return (
    <div className="rounded-[30px] border border-white/70 bg-white/90 p-8 shadow-panel">
      Loading…
    </div>
  );
}

function shell(page: ReactNode) {
  return (
    <AppShell>
      <Suspense fallback={<PageFallback />}>{page}</Suspense>
    </AppShell>
  );
}

export const router = createBrowserRouter(
  [
    {
      path: "/",
      element: shell(<OverviewPage />)
    },
    {
      path: "/products",
      element: shell(<ProductsPage />)
    },
    {
      path: "/products/:handle",
      element: shell(<ProductDetailPage />)
    },
    {
      path: "/collections",
      element: shell(<ContentListPage kind="collections" title="Collections" />)
    },
    {
      path: "/collections/:handle",
      element: shell(<ContentDetailPage kind="collections" />)
    },
    {
      path: "/pages",
      element: shell(<ContentListPage kind="pages" title="Pages" />)
    },
    {
      path: "/pages/:handle",
      element: shell(<ContentDetailPage kind="pages" />)
    },
    {
      path: "/blogs",
      element: shell(<BlogsPage />)
    },
    {
      path: "/blogs/:blogHandle",
      element: shell(<BlogArticlesPage />)
    },
    {
      path: "/articles",
      element: shell(<ArticlesPage />)
    },
    {
      path: "/articles/:blogHandle/:articleHandle",
      element: shell(<ArticleDetailPage />)
    },
    {
      path: "/keywords",
      element: shell(<KeywordsPage />)
    },
    {
      path: "/keywords/clusters/:id",
      element: shell(<ClusterDetailPage />)
    },
    {
      path: "/keywords/competitors/:domain",
      element: shell(<CompetitorDetailPage />)
    },
    {
      path: "/article-ideas",
      element: shell(<ArticleIdeasPage />)
    },
    {
      path: "/article-ideas/:ideaId",
      element: shell(<IdeaDetailPage />)
    },
    {
      path: "/google-ads-lab",
      element: shell(<GoogleAdsLabPage />)
    },
    {
      path: "/embeddings",
      element: shell(<EmbeddingsPage />)
    },
    {
      path: "/image-seo",
      element: shell(<ImageSeoPage />)
    },
    {
      path: "/api-usage",
      element: shell(<ApiUsagePage />)
    },
    {
      path: "/settings",
      element: shell(<SettingsPage />)
    }
  ],
  { basename: "/app" }
);
