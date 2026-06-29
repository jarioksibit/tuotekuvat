from __future__ import annotations

import argparse
import hashlib
import json
import math
import mimetypes
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import boto3
from boto3.exceptions import S3UploadFailedError
from botocore.exceptions import ClientError, NoCredentialsError
from PIL import Image, ImageOps


SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
TRAILING_INDEX_RE = re.compile(r"-(\d+)$")


@dataclass(frozen=True)
class ImageJob:
    source: Path
    relative_path: Path
    output: Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Optimize product images for web delivery and reuse."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_common_io_args(active_parser: argparse.ArgumentParser) -> None:
        active_parser.add_argument(
            "--input",
            default="input",
            help="Folder that contains the source images.",
        )
        active_parser.add_argument(
            "--output",
            default="optimized",
            help="Folder where optimized images are written.",
        )

    def add_r2_args(active_parser: argparse.ArgumentParser) -> None:
        active_parser.add_argument(
            "--r2-bucket",
            help="R2 bucket name to upload into. Required when uploading.",
        )
        active_parser.add_argument(
            "--r2-prefix",
            default="",
            help="Optional key prefix inside the bucket, for example products/images.",
        )
        active_parser.add_argument(
            "--r2-endpoint",
            help="Custom S3 endpoint for R2, for example https://<accountid>.r2.cloudflarestorage.com.",
        )
        active_parser.add_argument(
            "--r2-region",
            default="auto",
            help="AWS region value used by the S3 client. Cloudflare R2 typically uses auto.",
        )

    optimize_parser = subparsers.add_parser(
        "optimize",
        help="Optimize images locally and optionally upload the results to R2.",
    )
    upload_parser = subparsers.add_parser(
        "upload",
        help="Upload an already optimized folder to R2 without reprocessing images.",
    )
    mapping_parser = subparsers.add_parser(
        "map",
        help="Generate a partial handle/url JSON mapping from the image filenames.",
    )

    add_common_io_args(optimize_parser)
    add_common_io_args(upload_parser)
    add_common_io_args(mapping_parser)
    add_r2_args(optimize_parser)
    add_r2_args(upload_parser)

    optimize_parser.add_argument(
        "--upload-r2",
        action="store_true",
        help="Upload the optimized files to Cloudflare R2 after processing.",
    )

    mapping_parser.add_argument(
        "--mapping-output",
        help="Optional file path where the generated JSON should be written. Defaults to stdout.",
    )
    parser.add_argument(
        "--max-width",
        type=int,
        default=1600,
        help="Maximum width in pixels.",
    )
    parser.add_argument(
        "--max-height",
        type=int,
        default=1600,
        help="Maximum height in pixels.",
    )
    parser.add_argument(
        "--format",
        choices=("webp", "jpeg", "png"),
        default="webp",
        help="Output format for optimized files.",
    )
    parser.add_argument(
        "--quality",
        type=int,
        default=82,
        help="Output quality for lossy formats.",
    )
    parser.add_argument(
        "--fit",
        choices=("contain", "cover"),
        default="contain",
        help="Resize strategy. 'contain' keeps the whole image; 'cover' crops to the exact max size.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Rebuild all files instead of using the cache.",
    )
    return parser.parse_args()


def iter_image_files(root: Path, excluded_root: Path | None = None) -> Iterable[Path]:
    for path in root.rglob("*"):
        if excluded_root is not None and path.is_relative_to(excluded_root):
            continue
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS:
            yield path


def file_fingerprint(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def load_cache(cache_path: Path) -> dict[str, dict[str, str]]:
    if not cache_path.exists():
        return {}
    try:
        return json.loads(cache_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def save_cache(cache_path: Path, cache: dict[str, dict[str, str]]) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        json.dumps(cache, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def build_job(source: Path, input_root: Path, output_root: Path, target_format: str) -> ImageJob:
    relative_path = source.relative_to(input_root)
    output_relative = relative_path.with_suffix(f".{target_format}")
    output = output_root / output_relative
    return ImageJob(source=source, relative_path=relative_path, output=output)


def normalize_handle_candidate(stem: str) -> str:
    trimmed = stem.rstrip("-_.")
    trimmed = TRAILING_INDEX_RE.sub("", trimmed)
    return trimmed.rstrip("-_.")


def sort_image_stems(stems: Iterable[str]) -> list[str]:
    def sort_key(stem: str) -> tuple[str, int, str]:
        match = TRAILING_INDEX_RE.search(stem)
        index = int(match.group(1)) if match else math.inf
        base = TRAILING_INDEX_RE.sub("", stem)
        return base, index, stem

    return sorted(stems, key=sort_key)


def build_mapping_manifest(root: Path) -> list[dict[str, list[str]]]:
    groups: dict[str, list[str]] = {}

    for path in iter_image_files(root):
        stem = path.stem.strip()
        handle = normalize_handle_candidate(stem)
        if not handle:
            continue
        groups.setdefault(handle, []).append(stem)

    manifest: list[dict[str, list[str]]] = []
    for handle in sorted(groups):
        manifest.append({"handle": handle, "urls": sort_image_stems(groups[handle])})

    return manifest


def write_mapping_manifest(manifest: list[dict[str, list[str]]], output_path: str | None) -> None:
    payload = json.dumps(manifest, indent=2, ensure_ascii=False)
    if output_path:
        Path(output_path).write_text(payload + "\n", encoding="utf-8")
        print(f"Wrote mapping candidates to: {Path(output_path).resolve()}")
        return
    print(payload)


def prepare_image(image: Image.Image, fit: str, max_width: int, max_height: int) -> Image.Image:
    image = ImageOps.exif_transpose(image)
    if fit == "cover":
        return ImageOps.fit(image, (max_width, max_height), Image.Resampling.LANCZOS)
    return ImageOps.contain(image, (max_width, max_height), Image.Resampling.LANCZOS)


def normalize_mode(image: Image.Image, target_format: str) -> Image.Image:
    if target_format == "jpeg" and image.mode in {"RGBA", "LA", "P"}:
        background = Image.new("RGB", image.size, (255, 255, 255))
        if image.mode == "P":
            image = image.convert("RGBA")
        background.paste(image, mask=image.getchannel("A") if "A" in image.getbands() else None)
        return background

    if target_format == "jpeg":
        return image.convert("RGB")

    if image.mode == "P":
        return image.convert("RGBA")

    return image


def save_image(image: Image.Image, output_path: Path, target_format: str, quality: int) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if target_format == "webp":
        image.save(output_path, format="WEBP", quality=quality, method=6)
        return

    if target_format == "jpeg":
        image.save(output_path, format="JPEG", quality=quality, optimize=True, progressive=True)
        return

    image.save(output_path, format="PNG", optimize=True)


def build_r2_key(prefix: str, relative_path: Path) -> str:
    normalized_prefix = prefix.strip("/")
    relative_key = str(relative_path).replace(os.sep, "/")
    if not normalized_prefix:
        return relative_key
    return f"{normalized_prefix}/{relative_key}"


def upload_to_r2(output_root: Path, bucket_name: str, endpoint_url: str | None, region_name: str, prefix: str) -> int:
    access_key = os.getenv("CLOUDFLARE_R2_ACCESS_KEY") or os.getenv("AWS_ACCESS_KEY_ID")
    secret_key = os.getenv("CLOUDFLARE_R2_SECRET_KEY") or os.getenv("AWS_SECRET_ACCESS_KEY")

    if not access_key or not secret_key:
        raise SystemExit(
            "Cloudflare R2 upload needs credentials in the current shell. "
            "Set CLOUDFLARE_R2_ACCESS_KEY and CLOUDFLARE_R2_SECRET_KEY (or AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY)."
        )

    client = boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        region_name=region_name,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
    )
    uploaded = 0

    for path in output_root.rglob("*"):
        if not path.is_file() or path.name == ".image-opt-cache.json":
            continue

        content_type, _ = mimetypes.guess_type(path.name)
        extra_args = {}
        if content_type:
            extra_args["ContentType"] = content_type

        try:
            client.upload_file(
                Filename=str(path),
                Bucket=bucket_name,
                Key=build_r2_key(prefix, path.relative_to(output_root)),
                ExtraArgs=extra_args,
            )
        except NoCredentialsError as exc:
            raise SystemExit(
                "Cloudflare R2 upload needs AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY set in the current shell. "
                "In PowerShell, use `$env:AWS_ACCESS_KEY_ID = '...'` and `$env:AWS_SECRET_ACCESS_KEY = '...'` "
                "before running the command."
            ) from exc
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "Unknown")
            if code == "AccessDenied":
                raise SystemExit(
                    "R2 rejected PutObject with AccessDenied. Check that the provided key pair has write access to this bucket "
                    "and that bucket permissions allow uploading to the selected prefix."
                ) from exc
            raise
        except S3UploadFailedError as exc:
            message = str(exc)
            if "Credential access key has length" in message:
                raise SystemExit(
                    "Invalid R2 access key format. Use the R2 Access Key ID (32 chars), not an API token. "
                    "Set CLOUDFLARE_R2_ACCESS_KEY and CLOUDFLARE_R2_SECRET_KEY from an R2 API token pair."
                ) from exc
            if "AccessDenied" in message:
                raise SystemExit(
                    "R2 denied PutObject. The key pair is valid but lacks write permission for this bucket/prefix. "
                    "Grant Object Write on bucket '"
                    f"{bucket_name}"
                    "' and retry."
                ) from exc
            raise
        uploaded += 1

    return uploaded


def main() -> int:
    args = parse_args()
    input_root = Path(args.input).resolve()
    output_root = Path(args.output).resolve()
    cache_path = output_root / ".image-opt-cache.json"
    cache = {} if args.force else load_cache(cache_path)

    if args.command == "map":
        manifest = build_mapping_manifest(input_root)
        write_mapping_manifest(manifest, args.mapping_output)
        return 0

    if args.command == "upload":
        if not args.r2_bucket:
            raise SystemExit("--r2-bucket is required for the upload command.")
        uploaded = upload_to_r2(
            output_root=output_root,
            bucket_name=args.r2_bucket,
            endpoint_url=args.r2_endpoint,
            region_name=args.r2_region,
            prefix=args.r2_prefix,
        )
        print(f"Uploaded {uploaded} file(s) to R2 bucket: {args.r2_bucket}")
        return 0

    jobs = [build_job(path, input_root, output_root, args.format) for path in iter_image_files(input_root, output_root)]
    processed = 0
    skipped = 0

    for job in jobs:
        fingerprint = file_fingerprint(job.source)
        cache_key = str(job.relative_path).replace(os.sep, "/")

        cached_entry = cache.get(cache_key)
        if (
            not args.force
            and cached_entry
            and cached_entry.get("fingerprint") == fingerprint
            and job.output.exists()
        ):
            skipped += 1
            continue

        with Image.open(job.source) as original:
            image = prepare_image(original, args.fit, args.max_width, args.max_height)
            image = normalize_mode(image, args.format)
            save_image(image, job.output, args.format, args.quality)

        cache[cache_key] = {
            "fingerprint": fingerprint,
            "output": str(job.output.relative_to(output_root)).replace(os.sep, "/"),
        }
        processed += 1

    save_cache(cache_path, cache)
    print(f"Processed {processed} image(s), skipped {skipped} unchanged image(s).")
    print(f"Optimized images are in: {output_root}")

    if args.upload_r2:
        if not args.r2_bucket:
            raise SystemExit("--r2-bucket is required when --upload-r2 is set.")
        uploaded = upload_to_r2(
            output_root=output_root,
            bucket_name=args.r2_bucket,
            endpoint_url=args.r2_endpoint,
            region_name=args.r2_region,
            prefix=args.r2_prefix,
        )
        print(f"Uploaded {uploaded} file(s) to R2 bucket: {args.r2_bucket}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())