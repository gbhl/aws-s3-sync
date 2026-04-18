# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Setup

Requires Python 3.12+ and `libvips` (system package).

```bash
python -m venv venv
. venv/bin/activate
pip install -r requirements.txt
```

Copy `config.sample.toml` to `config.toml` and fill in `bhl_api_key` and the `[rabbitmq]` section. AWS credentials are read from `~/.aws/credentials` (default profile).

**Scripts must be run from the project root** — `config.toml` is always loaded from the current working directory.

## Running the main script

```bash
python update-aws-item.py --identifier IDENTIFIER
python update-aws-item.py --identifier IDENTIFIER --ocr-only
python update-aws-item.py --identifier IDENTIFIER --images-only
python update-aws-item.py --identifier IDENTIFIER --stdout --verbose   # for debugging
```

## Architecture

The codebase has three layers:

**Entry point** — `update-aws-item.py` is the main script. It reads `config.toml`, sets up logging, and orchestrates the full pipeline for a single IA identifier: download scandata → download/normalize images → rename JP2s → convert to WebP → upload to S3.

**Domain model** — `bhl_object.py` contains the `BHL_Object` class, which resolves an IA identifier (or BHL Item ID) against the BHL API to determine whether it's an `item`, `part`, or `virtual_item`, and downloads per-page OCR. Virtual items are skipped. Parts without a BarCode in `part.txt` use their parent Item's BarCode/OCR prefix.

**Utilities** — `bhl_aws_common.py` provides `download_url()`, which streams a URL to a temp file with retry/backoff logic.

### Key behaviors to understand

- **Image processing uses two libraries**: pyvips is primary; Wand (ImageMagick) is the fallback for files pyvips can't handle. Both must be available.
- **Cache layout**: `cache/json/` (IA metadata), `cache/xml/` (scandata), `cache/jp2/` (JP2 zips), `cache/ocr/` (per-page OCR). Scandata is intentionally preserved and never re-downloaded unless missing.
- **Log files**: One log file per identifier at `logs/IDENTIFIER.log`. The root log handler is replaced at the start of each `update_item()` call.
- **OCR S3 key prefix** depends on BHL type: `ocr/item-XXXXXX/` or `ocr/part-XXXXXX/`. The combined full-text file is named `item-XXXXXX.txt` or `part-XXXXXX.txt` with no sequence in the name.
- **Scandata controls image sequencing**: JP2 files are renumbered sequentially, skipping pages where `<addToAccessFormats>` is `false`.

### systemd daemon (monitor-queue.py)

`monitor-queue.py` is a script that runs as a systemd service. It polls two RabbitMQ queues defined in `config.toml` (`[queues]` section: `new-items`, `updated-itemd`, `ocr-only`) and spawns `update-aws-item.py` subprocesses up to the `concurrency` limit. Messages in `ocr-only` get the `--ocr-only` flag.

## config.toml sections

| Section | Key fields |
|---|---|
| `[general]` | `bhl_api_key`, `cache_path`, `scratch_path`, `max_image_dimension` |
| `[logging]` | `path`, `filename` |
| `[webp_sizes]` | `large`, `medium`, `small`, `thumb` (px widths — do not change) |
| `[rabbitmq]` | `host`, `full-items-queue`, `ocr-only-queue`, `concurrency` |
