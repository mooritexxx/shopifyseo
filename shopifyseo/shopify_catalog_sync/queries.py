PRODUCTS_QUERY = """
query ProductsPage($first: Int!, $after: String) {
  products(first: $first, after: $after, sortKey: UPDATED_AT) {
    pageInfo {
      hasNextPage
      endCursor
    }
    edges {
      node {
        id
        legacyResourceId
        title
        handle
        vendor
        productType
        status
        createdAt
        updatedAt
        publishedAt
        descriptionHtml
        tags
        seo {
          title
          description
        }
        totalInventory
        tracksInventory
        category {
          fullName
        }
        onlineStoreUrl
        options {
          id
          name
          values
        }
        featuredImage {
          id
          altText
          url
          width
          height
        }
        media(first: 50, sortKey: POSITION) {
          edges {
            node {
              ... on MediaImage {
                id
                alt
                image {
                  url
                  width
                  height
                }
              }
            }
          }
        }
        images(first: 50) {
          edges {
            node {
              id
              altText
              url
              width
              height
            }
          }
        }
        metafields(first: 100) {
          edges {
            node {
              id
              namespace
              key
              type
              value
            }
          }
        }
        variants(first: 100) {
          edges {
            node {
              id
              legacyResourceId
              title
              sku
              barcode
              price
              compareAtPrice
              position
              inventoryPolicy
              inventoryQuantity
              taxable
              selectedOptions {
                name
                value
              }
              image {
                id
                url
                altText
              }
            }
          }
        }
      }
    }
  }
}
"""


PRODUCT_QUERY = """
query ProductById($id: ID!) {
  product(id: $id) {
    id
    legacyResourceId
    title
    handle
    vendor
    productType
    status
    createdAt
    updatedAt
    publishedAt
    descriptionHtml
    tags
    seo {
      title
      description
    }
    totalInventory
    tracksInventory
    category {
      fullName
    }
    onlineStoreUrl
    options {
      id
      name
      values
    }
    featuredImage {
      id
      altText
      url
      width
      height
    }
    media(first: 50, sortKey: POSITION) {
      edges {
        node {
          ... on MediaImage {
            id
            alt
            image {
              url
              width
              height
            }
          }
        }
      }
    }
    images(first: 50) {
      edges {
        node {
          id
          altText
          url
          width
          height
        }
      }
    }
    metafields(first: 100) {
      edges {
        node {
          id
          namespace
          key
          type
          value
        }
      }
    }
    variants(first: 100) {
      edges {
        node {
          id
          legacyResourceId
          title
          sku
          barcode
          price
          compareAtPrice
          position
          inventoryPolicy
          inventoryQuantity
          taxable
          selectedOptions {
            name
            value
          }
          image {
            id
            url
            altText
          }
        }
      }
    }
  }
}
"""


COLLECTIONS_QUERY = """
query CollectionsPage($first: Int!, $after: String) {
  collections(first: $first, after: $after, sortKey: UPDATED_AT) {
    pageInfo {
      hasNextPage
      endCursor
    }
    edges {
      node {
        id
        title
        handle
        updatedAt
        descriptionHtml
        image {
          id
          url
          altText
          width
          height
        }
        seo {
          title
          description
        }
        ruleSet {
          appliedDisjunctively
          rules {
            column
            condition
            relation
          }
        }
        metafields(first: 100) {
          edges {
            node {
              id
              namespace
              key
              type
              value
            }
          }
        }
      }
    }
  }
}
"""


COLLECTION_QUERY = """
query CollectionById($id: ID!) {
  collection(id: $id) {
    id
    title
    handle
    updatedAt
    descriptionHtml
    image {
      id
      url
      altText
      width
      height
    }
    seo {
      title
      description
    }
    ruleSet {
      appliedDisjunctively
      rules {
        column
        condition
        relation
      }
    }
    metafields(first: 100) {
      edges {
        node {
          id
          namespace
          key
          type
          value
        }
      }
    }
  }
}
"""


PAGES_QUERY = """
query PagesPage($first: Int!, $after: String) {
  pages(first: $first, after: $after, sortKey: UPDATED_AT) {
    pageInfo {
      hasNextPage
      endCursor
    }
    edges {
      node {
        id
        title
        handle
        updatedAt
        templateSuffix
        body
        titleTag: metafield(namespace: "global", key: "title_tag") {
          id
          namespace
          key
          type
          value
        }
        descriptionTag: metafield(namespace: "global", key: "description_tag") {
          id
          namespace
          key
          type
          value
        }
      }
    }
  }
}
"""


PAGE_QUERY = """
query PageById($id: ID!) {
  page(id: $id) {
    id
    title
    handle
    updatedAt
    templateSuffix
    body
    titleTag: metafield(namespace: "global", key: "title_tag") {
      id
      namespace
      key
      type
      value
    }
    descriptionTag: metafield(namespace: "global", key: "description_tag") {
      id
      namespace
      key
      type
      value
    }
  }
}
"""


ARTICLE_QUERY = """
query ArticleById($id: ID!) {
  article(id: $id) {
    id
    title
    handle
    body
    summary
    publishedAt
    updatedAt
    isPublished
    tags
    author {
      name
    }
    blog {
      id
      handle
    }
    image {
      altText
      url
      width
      height
    }
    titleTag: metafield(namespace: "global", key: "title_tag") {
      id
      namespace
      key
      type
      value
    }
    descriptionTag: metafield(namespace: "global", key: "description_tag") {
      id
      namespace
      key
      type
      value
    }
  }
}
"""


# BlogSortKeys are only HANDLE, ID, TITLE — not UPDATED_AT (invalid and breaks the query on current Admin API).
BLOGS_QUERY = """
query BlogsPage($first: Int!, $after: String) {
  blogs(first: $first, after: $after, reverse: true) {
    pageInfo {
      hasNextPage
      endCursor
    }
    edges {
      node {
        id
        title
        handle
        createdAt
        updatedAt
        commentPolicy
        tags
      }
    }
  }
}
"""


BLOG_QUERY = """
query BlogById($id: ID!) {
  blog(id: $id) {
    id
    title
    handle
    createdAt
    updatedAt
    commentPolicy
    tags
  }
}
"""


# Primary path: Blog.articles — no search syntax; every node belongs to this blog.
# (Root articles(search) can return nodes where nested blog { id } is missing or mismatched,
# so client-side filtering was dropping all rows.)
BLOG_ARTICLES_CONNECTION_QUERY = """
query BlogArticlesPage($id: ID!, $first: Int!, $after: String) {
  blog(id: $id) {
    id
    articles(first: $first, after: $after, reverse: true) {
      pageInfo {
        hasNextPage
        endCursor
      }
      edges {
        node {
          id
          title
          handle
          body
          summary
          publishedAt
          updatedAt
          isPublished
          tags
          author {
            name
          }
          blog {
            id
            handle
          }
          image {
            altText
            url
            width
            height
          }
          titleTag: metafield(namespace: "global", key: "title_tag") {
            value
          }
          descriptionTag: metafield(namespace: "global", key: "description_tag") {
            value
          }
        }
      }
    }
  }
}
"""

# Fallback: QueryRoot.articles with search (e.g. if connection ever fails).
ARTICLES_BY_BLOG_QUERY = """
query ArticlesByBlog($first: Int!, $after: String, $query: String!) {
  articles(first: $first, after: $after, query: $query, sortKey: UPDATED_AT, reverse: true) {
    pageInfo {
      hasNextPage
      endCursor
    }
    edges {
      node {
        id
        title
        handle
        body
        summary
        publishedAt
        updatedAt
        isPublished
        tags
        author {
          name
        }
        blog {
          id
          handle
        }
        image {
          altText
          url
          width
          height
        }
        titleTag: metafield(namespace: "global", key: "title_tag") {
          value
        }
        descriptionTag: metafield(namespace: "global", key: "description_tag") {
          value
        }
      }
    }
  }
}
"""


COLLECTION_PRODUCTS_QUERY = """
query CollectionProducts($id: ID!, $first: Int!, $after: String) {
  collection(id: $id) {
    id
    products(first: $first, after: $after) {
      pageInfo {
        hasNextPage
        endCursor
      }
      edges {
        node {
          id
          handle
          title
        }
      }
    }
  }
}
"""


METAOBJECTS_BY_IDS_QUERY = """
query MetaobjectsByIds($ids: [ID!]!) {
  nodes(ids: $ids) {
    ... on Metaobject {
      id
      type
      handle
      displayName
      updatedAt
      fields {
        key
        value
        type
      }
    }
  }
}
"""
