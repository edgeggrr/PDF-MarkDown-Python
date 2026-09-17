The “md.py” is used to convert PDF documents to Markdown.The “md.py” converts a PDF to Markdown.

Simple fast and terminal-native Python tool for converting pdf documents to structured Markdown (`.md`) file. Automatically recognizes the native selectable text for the documents, and falls back to Optical Character Recognition (OCR) in the case of scanned or image-only documents.

---

## Key Features

Comes with Dual Extraction Engine which runs fast extraction as text (using the package pdfplumber) and automatically launches the on-detection of the package pytesseract for image-only/pdf scanned pages.
Allows for single files, many explicit file paths, or entire directories (with recursion option `-r`).
Clean, inline processing plan reporting with progress indicators, processing plan tables built with rich.
* **Flexible Page Filtering**: Parse specific page ranges (e.g., `--pages "1-3,5,8-10"`).
Support for granular OCR control through –force-ocr, –no-ocr, –dpi and –lang (eng+fra).
Interactive Drag-and-Drop: Can be called without any arguments to run an interactive prompt to clean up dragged-and-dropped file path(s).

---

## System Dependencies

Make sure that you have the following system level dependencies installed and available in your system path, prior to running the script, `md.py`:

### 1. Poppler (Rasterization PDF documents & OCR)
Install poppler with Homebrew on macOS:
On Ubuntu/Debian systems: `sudo apt-get install -y poppler-utils`
For "Windows" use binaries from the [Release Archives page](https://github.com/oschwartz10612/poppler-windows/releases/) see the "Install" section for adding the "bin/" folder to your System "PATH".

### 2. The text extracted by this module will be used in case the OCR extraction failed.This module is for OCR extraction fallback.
To install tesseract on macOS, run the following command: `brew install tesseract`
For Ubuntu/Debian systems, the command is: `sudo apt-get install -y tesseract-ocr`
For Windows: download the installation file from the UB-Mannheim Tesseract Wiki and click on the binary.

---

## Python Requirements & Installation

Using Pip install the relevant Python packages:

```bash
pip install pdfplumber pytesseract pdf2image rich
