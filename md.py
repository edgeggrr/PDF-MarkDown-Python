#!/usr/bin/env python3
"""
PDF -> Markdown Converter
--------------------------
A small terminal app that converts PDFs to Markdown, extracting selectable
text directly and falling back to OCR for scanned/image-only pages.

Usage examples:
    python md.py                        interactive mode (or scans cwd)
    python md.py report.pdf
    python md.py ./scans -r -o out/     recurse into ./scans, write to out/
    python md.py a.pdf b.pdf --force-ocr --lang eng+fra
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pdfplumber
import pytesseract
from pdf2image import convert_from_path
from pdf2image.exceptions import (
    PDFInfoNotInstalledError,
    PDFPageCountError,
    PDFSyntaxError,
)
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.prompt import Prompt
from rich.table import Table

console = Console()

APP_NAME = "PDF -> Markdown"
APP_VERSION = "2.0"


# --------------------------------------------------------------------------- #
# Data structures
# --------------------------------------------------------------------------- #

@dataclass
class PageResult:
    number: int
    text: str = ""
    used_ocr: bool = False
    error: Optional[str] = None


@dataclass
class FileResult:
    path: Path
    output_path: Optional[Path] = None
    pages: list[PageResult] = field(default_factory=list)
    error: Optional[str] = None
    elapsed: float = 0.0

    @property
    def ocr_pages(self) -> int:
        return sum(1 for p in self.pages if p.used_ocr)

    @property
    def failed_pages(self) -> int:
        return sum(1 for p in self.pages if p.error)


# --------------------------------------------------------------------------- #
# Core extraction
# --------------------------------------------------------------------------- #

def parse_page_spec(spec: str, total_pages: int) -> list[int]:
    """Parse a spec like '1-3,5,8-10' into a sorted list of 0-indexed pages."""
    pages: set[int] = set()
    for chunk in spec.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "-" in chunk:
            start, end = chunk.split("-", 1)
            start_i = int(start) if start else 1
            end_i = int(end) if end else total_pages
            pages.update(range(start_i, end_i + 1))
        else:
            pages.add(int(chunk))
    return sorted(p - 1 for p in pages if 1 <= p <= total_pages)


def extract_page(page, page_number: int, pdf_path: str, args) -> PageResult:
    """Extract one page's text, trying direct extraction before OCR.
    Never raises -- any failure is recorded on the returned PageResult so one
    bad page can't take down the rest of the file.
    """
    result = PageResult(number=page_number)

    if not args.force_ocr:
        try:
            text = page.extract_text()
        except Exception as e:  # malformed page content streams, etc.
            text = None
            result.error = f"text extraction failed: {e}"
        if text and text.strip():
            result.text = text.strip()
            result.error = None
            return result

    if args.no_ocr:
        if not result.error:
            result.error = "no selectable text and OCR is disabled"
        return result

    try:
        images = convert_from_path(
            pdf_path, first_page=page_number, last_page=page_number, dpi=args.dpi
        )
    except PDFInfoNotInstalledError:
        result.error = "poppler isn't installed (required for OCR rendering)"
        return result
    except (PDFPageCountError, PDFSyntaxError) as e:
        result.error = f"couldn't rasterize page for OCR: {e}"
        return result
    except Exception as e:
        result.error = f"OCR rasterization failed: {e}"
        return result

    if not images:
        result.error = "OCR rasterization returned no image"
        return result

    try:
        ocr_text = pytesseract.image_to_string(images[0], lang=args.lang)
    except pytesseract.TesseractNotFoundError:
        result.error = "tesseract isn't installed or not on PATH"
        return result
    except Exception as e:
        result.error = f"OCR failed: {e}"
        return result

    result.text = ocr_text.strip()
    result.used_ocr = True
    if result.text:
        result.error = None  # OCR recovered the page; clear any earlier extraction error
    else:
        result.error = "OCR produced no text (page may be blank)"
    return result


def extract_pdf(pdf_path: Path, args, progress: Progress, task_id) -> FileResult:
    result = FileResult(path=pdf_path)
    start = time.perf_counter()

    try:
        with pdfplumber.open(pdf_path) as pdf:
            total_pages = len(pdf.pages)
            page_indices = range(total_pages)
            if args.pages:
                page_indices = parse_page_spec(args.pages, total_pages)
            progress.update(task_id, total=len(page_indices) or 1)

            for idx in page_indices:
                page = pdf.pages[idx]
                page_result = extract_page(page, idx + 1, str(pdf_path), args)
                result.pages.append(page_result)
                progress.update(task_id, advance=1)
    except Exception as e:
        result.error = f"couldn't open PDF: {e}"

    result.elapsed = time.perf_counter() - start
    return result


def render_markdown(result: FileResult) -> str:
    parts = []
    for p in result.pages:
        if p.text:
            marker = " 🔎" if p.used_ocr else ""
            parts.append(f"## Page {p.number}{marker}\n\n{p.text}\n")
        elif p.error:
            parts.append(f"## Page {p.number} ⚠️\n\n*[extraction failed: {p.error}]*\n")
    return "\n---\n\n".join(parts)


# --------------------------------------------------------------------------- #
# File discovery & path handling
# --------------------------------------------------------------------------- #

def clean_pasted_path(raw: str) -> str:
    """Strip quotes/escapes that terminals often add when a path is dragged in."""
    raw = raw.strip().strip('"').strip("'")
    return raw.replace("\\ ", " ")


def discover_pdfs(inputs: list[str], recursive: bool) -> list[Path]:
    found: list[Path] = []
    for raw in inputs:
        p = Path(clean_pasted_path(raw)).expanduser()
        if p.is_dir():
            pattern = "**/*.pdf" if recursive else "*.pdf"
            found.extend(sorted(p.glob(pattern)))
        elif p.is_file() and p.suffix.lower() == ".pdf":
            found.append(p)
        elif not p.exists():
            console.print(f"[bold red]Path not found, skipping:[/bold red] {p}")
    seen: set[Path] = set()
    unique: list[Path] = []
    for f in found:
        rf = f.resolve()
        if rf not in seen:
            seen.add(rf)
            unique.append(f)
    return unique


def resolve_output_path(pdf_path: Path, output_dir: Optional[Path]) -> Path:
    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)
        return output_dir / (pdf_path.stem + ".md")
    return pdf_path.with_suffix(".md")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="md",
        description="Convert PDFs to Markdown, with automatic OCR fallback for scanned pages.",
    )
    parser.add_argument(
        "inputs", nargs="*",
        help="PDF file(s) and/or folder(s) to convert. Defaults to the current folder.",
    )
    parser.add_argument("-o", "--output-dir", type=str, default=None,
                         help="Write all .md files here instead of next to each PDF.")
    parser.add_argument("-r", "--recursive", action="store_true",
                         help="Search folders recursively for PDFs.")
    parser.add_argument("--pages", type=str, default=None,
                         help="Only convert these pages, e.g. '1-3,5,8-10'.")
    parser.add_argument("--dpi", type=int, default=200,
                         help="DPI used when rasterizing pages for OCR (default: 200).")
    parser.add_argument("--lang", type=str, default="eng",
                         help="Tesseract language code(s), e.g. 'eng' or 'eng+fra'.")
    parser.add_argument("--force-ocr", action="store_true",
                         help="OCR every page, even if it already has selectable text.")
    parser.add_argument("--no-ocr", action="store_true",
                         help="Never fall back to OCR; skip pages with no selectable text.")
    parser.add_argument("--skip-existing", dest="overwrite", action="store_false", default=True,
                         help="Skip PDFs whose output .md file already exists.")
    parser.add_argument("-q", "--quiet", action="store_true",
                         help="Only print the final summary table.")
    parser.add_argument("--debug", action="store_true",
                         help="Show full Python tracebacks on unexpected errors.")
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {APP_VERSION}")
    return parser


def prompt_for_inputs() -> list[str]:
    console.print()
    raw = Prompt.ask(
        "[bold cyan]Drag & drop a PDF or folder here, or press Enter to scan the current folder[/bold cyan]",
        default="",
    )
    return [raw] if raw.strip() else ["."]


# --------------------------------------------------------------------------- #
# Display
# --------------------------------------------------------------------------- #

def print_header() -> None:
    console.print(
        Panel.fit(
            f"[bold cyan]{APP_NAME}[/bold cyan]  [dim]v{APP_VERSION}[/dim]\n"
            "[dim]Lossless text extraction, with OCR for scanned pages[/dim]",
            border_style="cyan",
        )
    )


def format_size(num_bytes: int) -> str:
    kb = num_bytes / 1024
    return f"{kb:,.0f} KB" if kb < 1024 else f"{kb / 1024:,.1f} MB"


def print_plan_table(files: list[Path], output_dir: Optional[Path]) -> None:
    table = Table(title="Files to process", header_style="bold magenta")
    table.add_column("PDF", style="cyan")
    table.add_column("Size", style="dim", justify="right")
    table.add_column("Output", style="green")
    for f in files:
        try:
            size_str = format_size(f.stat().st_size)
        except OSError:
            size_str = "?"
        table.add_row(f.name, size_str, str(resolve_output_path(f, output_dir)))
    console.print(table)
    console.print()


def print_summary(results: list[FileResult]) -> None:
    table = Table(title="Summary", header_style="bold magenta")
    table.add_column("PDF", style="cyan")
    table.add_column("Pages", justify="right")
    table.add_column("OCR'd", justify="right", style="yellow")
    table.add_column("Failed", justify="right", style="red")
    table.add_column("Time", justify="right", style="dim")
    table.add_column("Status")

    clean = partial = failed = 0
    for r in results:
        if r.error:
            status = "[bold red]error[/bold red]"
            failed += 1
        elif r.failed_pages:
            status = "[bold yellow]partial[/bold yellow]"
            partial += 1
        else:
            status = "[bold green]done[/bold green]"
            clean += 1
        table.add_row(
            r.path.name,
            "-" if r.error else str(len(r.pages)),
            "-" if r.error else str(r.ocr_pages),
            "-" if r.error else str(r.failed_pages),
            f"{r.elapsed:.1f}s",
            status,
        )
    console.print(table)

    if not results:
        return
    total = len(results)
    if clean == total:
        style, msg = "green", "All conversions finished successfully!"
    elif failed == total:
        style, msg = "red", "All conversions failed."
    else:
        bits = [f"{n} {label}" for n, label in ((clean, "clean"), (partial, "partial"), (failed, "failed")) if n]
        style = "red" if failed else "yellow"
        msg = f"Finished: {', '.join(bits)} (of {total})."
    console.print(Panel(f"[bold {style}]{msg}[/bold {style}]", border_style=style))


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def main() -> None:
    parser = build_arg_parser()
    args = parser.parse_args()

    if not args.quiet:
        print_header()

    inputs = args.inputs
    if not inputs:
        inputs = prompt_for_inputs() if sys.stdin.isatty() else ["."]

    pdf_files = discover_pdfs(inputs, args.recursive)
    if not pdf_files:
        console.print("[bold red]No PDF files found.[/bold red]")
        sys.exit(1)

    output_dir = Path(args.output_dir).expanduser() if args.output_dir else None

    if not args.overwrite:
        remaining = []
        for f in pdf_files:
            if resolve_output_path(f, output_dir).exists():
                console.print(f"[dim]Skipping (already converted): {f.name}[/dim]")
            else:
                remaining.append(f)
        pdf_files = remaining
        if not pdf_files:
            console.print("[yellow]Nothing to do -- every file is already converted.[/yellow]")
            return

    if not args.quiet:
        print_plan_table(pdf_files, output_dir)

    results: list[FileResult] = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        for pdf_file in pdf_files:
            task_id = progress.add_task(f"[yellow]{pdf_file.name}[/yellow]", total=1)
            try:
                result = extract_pdf(pdf_file, args, progress, task_id)
            except KeyboardInterrupt:
                raise
            except Exception as e:
                result = FileResult(path=pdf_file, error=str(e))
                if args.debug:
                    console.print_exception()

            if not result.error:
                markdown = render_markdown(result)
                if markdown.strip():
                    out_path = resolve_output_path(pdf_file, output_dir)
                    try:
                        out_path.write_text(markdown, encoding="utf-8")
                        result.output_path = out_path
                    except OSError as e:
                        result.error = f"couldn't write output: {e}"
                else:
                    result.error = "no text could be extracted from any page"

            results.append(result)

            if not args.quiet:
                if result.error:
                    console.print(f"[bold red]x {pdf_file.name}:[/bold red] {result.error}")
                elif result.failed_pages:
                    console.print(
                        f"[bold yellow]! {pdf_file.name}[/bold yellow] -> {result.output_path.name} "
                        f"[dim]({result.failed_pages} page(s) failed)[/dim]"
                    )
                else:
                    console.print(f"[bold green]v {pdf_file.name}[/bold green] -> {result.output_path.name}")

    console.print()
    print_summary(results)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n[bold yellow]Cancelled.[/bold yellow]")
        sys.exit(130)
