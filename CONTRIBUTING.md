# Contributing & Support

Thanks for using the DArT Order Downloader. This is a small tool for downloading
the data associated with a DArT order.

## I just want to download my order

See the [README](README.md) for installation and usage. In short:

```bash
pip install -r requirements.txt
python -m dart_downloader
```

You'll need your **order number** and a **share token**.

### Getting a share token

Generate one here: https://ordering.diversityarrays.com/token.pl

## Reporting a problem

If something isn't working:

1. Re-run with the `--debug` flag to get verbose output:
   ```bash
   python -m dart_downloader --debug
   ```
2. Open an issue on this repository and include:
   - What you ran and what happened (copy the terminal output; **remove your
     share token** first — treat it like a password).
   - Your operating system and Python version (`python --version`).
   - The `download_report.txt` from your download folder, if one was produced.

Please do **not** include your share token, and do not attach real genomic data
files.

## Reporting a security issue

Please do not open a public issue for security problems. See
[SECURITY.md](SECURITY.md) for how to report them privately.

## Development

```bash
pip install -r requirements-dev.txt
pytest
```

Tests must pass on Python 3.12+ (see the CI workflow in
`.github/workflows/tests.yml`). Please keep changes small and add a test for any
behaviour change.
