# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html) where practical.

## [Unreleased]

### Added

- Settings UI connection status badges and “Test connection” for Shopify Admin (`POST /api/settings/shopify-test`), with optional credential overrides from the form.
- DataForSEO validation accepts optional login/password in the request body (values from the form before save).
- Open-source contributor docs: `CODE_OF_CONDUCT.md`, `SECURITY.md`, issue/PR templates, `docs/ARCHITECTURE.md`, `Makefile`, and CI workflow for a minimal API smoke test and frontend typecheck.
