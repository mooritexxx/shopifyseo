# Security policy

## Supported versions

Security fixes are applied to the default branch (`main`) and may be backported to release branches at maintainers’ discretion.

## Reporting a vulnerability

**Please do not open a public GitHub issue for security reports.**

If you believe you have found a security vulnerability in this project (for example, unsafe handling of credentials, injection, or authentication bypass), report it privately so we can coordinate a fix before public disclosure.

1. Open a [GitHub Security Advisory](https://docs.github.com/en/code-security/security-advisories/about-github-security-advisories) for this repository, **or**
2. Email the repository maintainers if an email is listed in the repo profile or README.

Include:

- A short description of the issue and its impact
- Steps to reproduce (proof-of-concept if possible)
- Affected versions or commit, if known

We aim to acknowledge reports within a few business days and will work with you on a disclosure timeline.

## Scope

In scope: the application code in this repository as run in a typical self-hosted deployment (FastAPI backend, React frontend, local SQLite).

Out of scope: third-party services (Shopify, Google, AI providers), vulnerabilities in your OS or Python/Node installations unless they are directly triggered by this project’s documented usage.

## Safe handling of secrets

This app stores API keys and OAuth tokens in a local SQLite database and may mirror them into process environment variables at runtime. Protect the database file and machine access like any production credential store.
