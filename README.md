# Image optimization

This folder includes a reusable script for preparing product images for web hosting and Cloudflare R2.

## What it does

- Resizes images to fit within a maximum width and height.
- Converts images to a consistent output format.
- Keeps transparency when the output format supports it.
- Skips unchanged images on later runs using a cache file.

## Install

```bash
pip install -r requirements.txt
```

## Run

```bash with defaults
python3 optimize_images.py optimize
```

```bash
python3 optimize_images.py optimize --input input --output optimized
```

Useful options:

- `--max-width 1600`
- `--max-height 1600`
- `--format webp`
- `--quality 82`
- `--fit contain`
- `--force`

The optimized files are written into the output folder, and the script can be run again whenever new images are added.

## Upload to Cloudflare R2

Use S3-compatible Cloudflare R2 credentials:

- `Access Key ID`
- `Secret Access Key`
- `Endpoint`

This script uses S3-compatible upload only. A Cloudflare API token is not used by this script.

Quick start in Linux / WSL2 (bash):

```bash
export CLOUDFLARE_R2_ACCESS_KEY="your_access_key_id"
export CLOUDFLARE_R2_SECRET_KEY="your_secret_access_key"
export CLOUDFLARE_R2_ENDPOINT="https://<accountid>.r2.cloudflarestorage.com"

source .venv/bin/activate

python optimize_images.py optimize --upload-r2 --r2-bucket kuullos-dev --r2-endpoint "$CLOUDFLARE_R2_ENDPOINT" --r2-prefix product_images
python optimize_images.py optimize --upload-r2 --r2-bucket kuullos-prod --r2-endpoint "$CLOUDFLARE_R2_ENDPOINT" --r2-prefix product_images

python optimize_images.py optimize --input input --output optimized --upload-r2 --r2-bucket kuullos-prod --r2-endpoint "$CLOUDFLARE_R2_ENDPOINT" --r2-prefix product_images
```

Upload-only command in Linux / WSL2 (bash):

```bash
export CLOUDFLARE_R2_ACCESS_KEY="your_access_key_id"
export CLOUDFLARE_R2_SECRET_KEY="your_secret_access_key"
export CLOUDFLARE_R2_ENDPOINT="https://<accountid>.r2.cloudflarestorage.com"

python optimize_images.py upload --output optimized --r2-bucket kuullos-prod --r2-endpoint "$CLOUDFLARE_R2_ENDPOINT" --r2-prefix product_images
```

Quick start in PowerShell:

```powershell
$env:CLOUDFLARE_R2_ACCESS_KEY = "your_access_key_id"
$env:CLOUDFLARE_R2_SECRET_KEY = "your_secret_access_key"
$env:CLOUDFLARE_R2_ENDPOINT = "https://<accountid>.r2.cloudflarestorage.com"

python optimize_images.py optimize --input input --output optimized --upload-r2 --r2-bucket kuullos-prod --r2-endpoint $env:CLOUDFLARE_R2_ENDPOINT --r2-prefix product_images
```

Upload-only command in PowerShell::

```powershell
python optimize_images.py upload --output optimized --r2-bucket kuullos-prod --r2-endpoint $env:CLOUDFLARE_R2_ENDPOINT --r2-prefix product_images
```

```powershell
$env:CLOUDFLARE_R2_ACCESS_KEY = "your_access_key_id"
$env:CLOUDFLARE_R2_SECRET_KEY = "your_secret_access_key"
$env:CLOUDFLARE_R2_ENDPOINT = "https://<accountid>.r2.cloudflarestorage.com"
python optimize_images.py optimize --input . --output optimized --upload-r2 --r2-bucket your-bucket --r2-endpoint $env:CLOUDFLARE_R2_ENDPOINT --r2-prefix product_images
```

```bash
export CLOUDFLARE_R2_ACCESS_KEY="your_access_key_id"
export CLOUDFLARE_R2_SECRET_KEY="your_secret_access_key"
export CLOUDFLARE_R2_ENDPOINT="https://<accountid>.r2.cloudflarestorage.com"
python optimize_images.py optimize --input . --output optimized --upload-r2 --r2-bucket your-bucket --r2-endpoint "$CLOUDFLARE_R2_ENDPOINT" --r2-prefix product_images
```

The script also accepts `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` if you prefer those names.

If you do not want a prefix, omit `--r2-prefix`. The upload step sends the optimized files from the output folder to the bucket after the local optimization finishes.

## Upload-only command

If the optimized files already exist and you only want to push them again to R2, use the dedicated upload command:

```powershell
$env:CLOUDFLARE_R2_ACCESS_KEY = "your_access_key_id"
$env:CLOUDFLARE_R2_SECRET_KEY = "your_secret_access_key"
$env:CLOUDFLARE_R2_ENDPOINT = "https://<accountid>.r2.cloudflarestorage.com"
python optimize_images.py upload --output optimized --r2-bucket your-bucket --r2-endpoint $env:CLOUDFLARE_R2_ENDPOINT --r2-prefix product_images
```

```bash
export CLOUDFLARE_R2_ACCESS_KEY="your_access_key_id"
export CLOUDFLARE_R2_SECRET_KEY="your_secret_access_key"
export CLOUDFLARE_R2_ENDPOINT="https://<accountid>.r2.cloudflarestorage.com"
python optimize_images.py upload --output optimized --r2-bucket your-bucket --r2-endpoint "$CLOUDFLARE_R2_ENDPOINT" --r2-prefix product_images
```

This skips image processing and uploads whatever is already in the output folder.

## Generate mapping candidates

If you want a first-pass JSON mapping for your database records, the script can generate SKU-aware candidates (`kuullos_sku`) with handle and URLs:

For preview
```bash
python optimize_images.py map --input optimized --url-prefix "https://assets.kuullos.fi/product_images/" --url-postfix ".webp"
```

For mapping file generation
```bash
python optimize_images.py map --input optimized --url-prefix "https://assets.kuullos.fi/product_images/" --url-postfix ".webp" --mapping-output mapping.candidates.json
```

By default, the map command looks up SKUs from `../kuullos-medusa/data/catalog/supplier-mapping.csv` using `kuullos_handle` and `kuullos_sku` columns. You can override this with:

- `--sku-source /path/to/supplier-mapping.csv`
- `--sku-handle-column kuullos_handle`
- `--sku-column kuullos_sku`

The generator strips only the trailing image index from filenames, so it works well for product images like `product-name-1`, `product-name-2`, and similar sets. If a SKU cannot be resolved uniquely, the entry is still emitted with `handle`/`urls` so you can complete it manually.