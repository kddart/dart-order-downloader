# Security Policy

## Reporting a vulnerability

If you discover a security issue in this tool, please report it **privately**
rather than opening a public issue.

Contact: **security@diversityarrays.com**

Please include enough detail to reproduce the issue. We'll acknowledge your
report and keep you informed as we work on a fix.

## Handling share tokens

A **share token** grants access to download an order's data. Treat it like a
password:

- Don't paste tokens into public issues, logs, screenshots, or chat.
- Don't commit tokens to source control.
- Generate a fresh token if you believe one has been exposed, via
  https://ordering.diversityarrays.com/token.pl

The tool sends your token only to the DArT ordering API over HTTPS and does not
store it on disk.

## Supported versions

This tool is distributed from `main`. Please make sure you're running the latest
version before reporting an issue.
