# DArT Order Downloader

A simple command-line tool for downloading the genomic data associated with your
DArT order. It downloads every available file for an order — using multiple
connections per large file where the server supports it — checks each file's
size, resumes from where it left off if interrupted, and writes a report when it
finishes.

## Requirements

- Python 3.12 or newer
- Your **order number**
- A **share token** for the order

Don't have a share token? Generate one here:
https://ordering.diversityarrays.com/token.pl

## Installation

Download or clone this repository, then open a terminal in the project folder
(the one containing `pyproject.toml`).

Install the dependencies:

```bash
pip install -r requirements.txt
```

That's it — there's nothing else to install.

> **Tip:** if you'd rather keep these packages isolated from the rest of your
> system, create a virtual environment first:
>
> ```bash
> python -m venv .venv
> source .venv/bin/activate        # Windows: .venv\Scripts\activate
> pip install -r requirements.txt
> ```

## Usage

From the project folder, run:

```bash
python -m dart_downloader
```

You'll be prompted for three things:

1. **Share Token** — the token for your order.
2. **Order Number** — the order you want to download.
3. **Download Folder** — where to save the files. Press Enter to accept the
   default (`./Order_<order_number>` in the current directory), or type any path
   you like (absolute or relative).

The tool then fetches your order details, shows a summary, and downloads all
available files with a live progress display — an overall bar plus a row per
active file showing its transfer rate and ETA. When it's done it validates the
files and prints a summary report.

### File validation

Every downloaded file is checked for completeness by comparing its size against
the expected size from your order. In addition, if your order includes an
`MD5SUMS` file, the tool downloads it first and verifies each file's content
against its MD5 checksum:

- **MD5 OK** — the file's checksum matched.
- **MD5 n/a** — the file has no entry in `MD5SUMS` (e.g. reports). It still
  passes on size; this is just noted.
- **MD5 MISMATCH** — the content didn't match. The tool re-downloads the file up
  to twice; if it still doesn't match, the file is reported as failed.

If your order has **no `MD5SUMS` file**, the tool cannot verify file contents —
it validates by **size only**. It warns you about this **before downloading
starts** and asks whether to continue, so you can press Ctrl-C (or answer no) to
abort and obtain an order with checksums first. If you continue, it also prints a
prominent warning at the end of the run, and the report records
`MD5 Verified: NOT AVAILABLE`, so you always know whether your download was
checksum-verified.

### Options

| Flag            | Default | Description                          |
|-----------------|---------|--------------------------------------|
| `--concurrency` | 8       | Maximum simultaneous connections     |
| `--host`        | `ordering.diversityarrays.com` | Ordering server to fetch the order from |
| `--debug`       | off     | Enable verbose debug logging         |

`--concurrency` caps the total number of simultaneous HTTP connections. These
are shared across the whole download: several small files can transfer at once,
and a single large file can be split into multiple byte-range segments that
download in parallel (when the server supports ranged requests). Either way, the
total number of live connections never exceeds this limit. The default of 8 is a
good balance; higher values mainly help on lossy, high-latency links (e.g.
satellite) where a single connection is throughput-limited.

Some files may be served from a DArT local server on a limited connection rather
than from cloud storage. Downloads of those files are automatically capped to at
most **4 at a time** (and never split across multiple connections), regardless
of `--concurrency`, to avoid saturating that link. Files in cloud storage are
unaffected and use the full connection budget. If you set `--concurrency` below
4, that lower value applies to these files too.

You normally won't need `--host` — it exists for testing against an alternate
ordering server. You can also set it via the `DART_ORDERING_HOST` environment
variable (the `--host` flag takes precedence). It accepts a bare host
(`ordering.diversityarrays.com`) or a full URL (`https://staging.example.com`).
It only affects which server the order details are fetched from; the individual
files are downloaded from the URLs that server returns.

Example:

```bash
python -m dart_downloader --concurrency 4 --debug
```

`--debug` output includes the order/file URLs it requests. Your share token is
**not** logged (it's sent as an HTTP header, which the logs redact), but if you
share debug output, review it first and remove anything you'd rather not post.

## Resuming an interrupted download

If the tool is interrupted (network drop, closed terminal, etc.), just run it
again and choose the **same download folder**. It detects the existing
`download_manifest.json` in that folder and resumes where it left off — already
completed files are not downloaded again.

Resume works at the **byte level**: a file that was only partially downloaded
picks up from where it stopped rather than starting over, so an interruption
near the end of a large file doesn't mean re-downloading the whole thing. (If the
server can't honour a resume request, the tool falls back to re-downloading that
file cleanly.)

On resume, completed files are re-checked by size, and any file that wasn't
already MD5-verified is re-verified against `MD5SUMS` (when the order includes
one). Files that already passed their MD5 check are trusted and not re-hashed.
Anything that now fails size or MD5 is re-downloaded.

## Output files

When the download completes, your download folder contains:

- The downloaded data files
- `download_manifest.json` — internal state used for resuming
- `download_report.json` — machine-readable summary
- `download_report.txt` — human-readable summary

## Troubleshooting

- **"Failed to fetch order"** — double-check your share token and order number,
  and confirm the token hasn't expired. Generate a new one if needed.
- **A file failed validation** — the downloaded size didn't match the expected
  size, or its MD5 checksum didn't match after re-downloading. Re-run the tool
  with the same folder to try again.

## Support & reporting problems

- Need a share token? Generate one at
  https://ordering.diversityarrays.com/token.pl
- Hit a problem? See [CONTRIBUTING.md](CONTRIBUTING.md) for how to report it
  (and please never share your token).
- Found a security issue? See [SECURITY.md](SECURITY.md) — report it privately,
  not via a public issue.

## For developers

Install the development dependencies and run the tests:

```bash
pip install -r requirements-dev.txt
pytest --cov=dart_downloader
```

### Project layout

```
dart_downloader/
├── __main__.py          # Entry point & orchestration
├── api/client.py        # DArT API client
├── models/models.py     # Data models
├── download/engine.py   # Concurrent download engine
├── validation/          # File validation (size + MD5)
├── state/manager.py     # Download state & resume
├── reporting/report.py  # Report generation
└── ui/terminal.py       # Terminal interface
```


## License

Copyright 2026 Diversity Arrays Technology.

Licensed under the Apache License, Version 2.0. See the [LICENSE](LICENSE) file
for the full text.
