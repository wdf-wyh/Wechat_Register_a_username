# Security Policy

## Supported versions

Security fixes are applied on a best-effort basis to the `main` branch.

## Reporting a vulnerability

Please **do not** open a public issue for secrets exposure, RCE in tooling, or ways to bypass platform protections.

Email or privately message the repository maintainers with:

- Affected component / commit
- Impact summary
- Minimal reproduction (no live account credentials)

## Secrets

Never commit API keys, DingTalk webhooks, device serials tied to personal accounts, or unredacted screenshots. Use `.env` (gitignored) and rotate keys if leaked.
