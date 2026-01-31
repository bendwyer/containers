#!/usr/bin/env python3
"""Convert manga EPUB files to CBZ by parsing the OPF spine for correct page order.

Inspired by https://github.com/bust4cap/epub2cbz

Created with Claude Opus 4.5

Usage:
    epub2cbz.py --input-dir /source/dir --output-dir /destination/dir \
                --min-size 50 --marker-dir /config/epub2cbz
"""

import argparse
import os
import re
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET


def find_opf_path(epub: zipfile.ZipFile) -> str:
    """Parse META-INF/container.xml to find the OPF file path."""
    container = epub.read("META-INF/container.xml")
    root = ET.fromstring(container)
    ns = {"c": "urn:oasis:names:tc:opendocument:xmlns:container"}
    rootfile = root.find(".//c:rootfile", ns)
    if rootfile is None:
        raise ValueError("No rootfile found in container.xml")
    return rootfile.attrib["full-path"]


def parse_opf(epub: zipfile.ZipFile, opf_path: str):
    """Parse the OPF manifest and spine to get ordered image paths."""
    opf_dir = str(Path(opf_path).parent)
    opf_data = epub.read(opf_path)
    root = ET.fromstring(opf_data)

    # Detect namespace (OPF 2.0 and 3.0 use the same namespace)
    ns = {"opf": "http://www.idpf.org/2007/opf"}

    # Build manifest: id -> href
    manifest = {}
    for item in root.findall(".//opf:manifest/opf:item", ns):
        item_id = item.attrib["id"]
        href = item.attrib["href"]
        media_type = item.attrib.get("media-type", "")
        manifest[item_id] = {"href": href, "media_type": media_type}

    # Get spine order
    spine = root.find(".//opf:spine", ns)
    if spine is None:
        raise ValueError("No spine found in OPF")

    spine_ids = []
    for itemref in spine.findall("opf:itemref", ns):
        spine_ids.append(itemref.attrib["idref"])

    # Resolve spine entries to image paths
    # Each spine entry points to an XHTML page that contains an image
    image_paths = []
    image_extensions = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff"}

    for item_id in spine_ids:
        if item_id not in manifest:
            continue
        entry = manifest[item_id]
        href = entry["href"]

        # Resolve path relative to OPF directory
        if opf_dir and opf_dir != ".":
            full_path = f"{opf_dir}/{href}"
        else:
            full_path = href

        # If the spine entry is an image, use it directly
        if Path(href).suffix.lower() in image_extensions:
            image_paths.append(full_path)
            continue

        # Otherwise it's an XHTML page - parse it to find embedded images
        try:
            page_data = epub.read(full_path)
        except KeyError:
            continue

        page_images = extract_images_from_xhtml(page_data, full_path)
        image_paths.extend(page_images)

    return image_paths


def extract_images_from_xhtml(xhtml_data: bytes, xhtml_path: str) -> list[str]:
    """Extract image paths from an XHTML page."""
    xhtml_dir = str(Path(xhtml_path).parent)
    images = []

    # Parse as XML, handling common XHTML namespaces
    try:
        root = ET.fromstring(xhtml_data)
    except ET.ParseError:
        return images

    # Search for img and image tags across namespaces
    xhtml_ns = "http://www.w3.org/1999/xhtml"
    svg_ns = "http://www.w3.org/2000/svg"
    xlink_ns = "http://www.w3.org/1999/xlink"

    # Find <img> tags
    for img in root.iter(f"{{{xhtml_ns}}}img"):
        src = img.attrib.get("src", "")
        if src:
            images.append(resolve_path(xhtml_dir, src))

    # Find <image> tags (SVG embedded images, common in manga EPUBs)
    for img in root.iter(f"{{{svg_ns}}}image"):
        href = img.attrib.get(f"{{{xlink_ns}}}href", "")
        if not href:
            href = img.attrib.get("href", "")
        if href:
            images.append(resolve_path(xhtml_dir, href))

    return images


def resolve_path(base_dir: str, relative: str) -> str:
    """Resolve a relative path against a base directory within the EPUB."""
    if base_dir and base_dir != ".":
        combined = f"{base_dir}/{relative}"
    else:
        combined = relative
    # Normalize ../  references
    parts = []
    for part in combined.split("/"):
        if part == "..":
            if parts:
                parts.pop()
        elif part and part != ".":
            parts.append(part)
    return "/".join(parts)


def convert_epub_to_cbz(epub_path: Path, output_path: Path) -> bool:
    """Convert a single EPUB to CBZ."""
    try:
        with zipfile.ZipFile(epub_path, "r") as epub:
            opf_path = find_opf_path(epub)
            image_paths = parse_opf(epub, opf_path)

            if not image_paths:
                print(f"  WARNING: No images found in {epub_path.name}", file=sys.stderr)
                return False

            with zipfile.ZipFile(output_path, "w", zipfile.ZIP_STORED) as cbz:
                for i, img_path in enumerate(image_paths):
                    try:
                        img_data = epub.read(img_path)
                    except KeyError:
                        print(
                            f"  WARNING: Image not found in EPUB: {img_path}",
                            file=sys.stderr,
                        )
                        continue

                    ext = Path(img_path).suffix.lower()
                    cbz_name = f"{i:04d}{ext}"
                    cbz.writestr(cbz_name, img_data)

            print(f"  OK: {len(image_paths)} images -> {output_path.name}")
            return True

    except Exception as e:
        print(f"  ERROR converting {epub_path.name}: {e}", file=sys.stderr)
        if output_path.exists():
            output_path.unlink()
        return False


def main():
    parser = argparse.ArgumentParser(description="Convert manga EPUBs to CBZ")
    parser.add_argument("--input-dir", required=True, help="Directory with EPUB files")
    parser.add_argument("--output-dir", required=True, help="Output directory for CBZ files")
    parser.add_argument(
        "--min-size",
        type=int,
        default=50,
        help="Minimum file size in MB to consider as manga (default: 50)",
    )
    parser.add_argument(
        "--marker-dir",
        required=True,
        help="Directory to store conversion markers",
    )
    args = parser.parse_args()

    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)
    marker_dir = Path(args.marker_dir)
    min_bytes = args.min_size * 1024 * 1024

    output_dir.mkdir(parents=True, exist_ok=True)
    marker_dir.mkdir(parents=True, exist_ok=True)

    epubs = sorted(input_dir.glob("*.epub"))
    if not epubs:
        print("No EPUB files found")
        return

    converted = 0
    skipped = 0

    for epub_path in epubs:
        size = epub_path.stat().st_size
        if size < min_bytes:
            continue

        # Check if already converted
        marker = marker_dir / f"{epub_path.name}.converted"
        if marker.exists():
            skipped += 1
            continue

        cbz_name = re.sub(r"\s+-\s+[^-]+$", "", epub_path.stem) + ".cbz"
        output_path = output_dir / cbz_name

        print(f"Converting: {epub_path.name} ({size // (1024*1024)}MB)")

        if convert_epub_to_cbz(epub_path, output_path):
            marker.touch()
            converted += 1

    print(f"\nDone: {converted} converted, {skipped} skipped (already converted)")


if __name__ == "__main__":
    main()
