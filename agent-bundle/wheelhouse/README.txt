OFFLINE WHEEL BUNDLE - fill this folder before shipping to the air-gapped machine
==================================================================================

This folder is intentionally EMPTY in the project. The wheels are large and
platform-specific, so they are produced once on a connected machine.

HOW TO FILL IT
--------------
On a machine that CAN reach PyPI (your work box reaches it through Zscaler /
truststore), with the SAME OS and SAME Python (major.minor) as the TARGET
laptop, run from the project root:

    python make_wheelhouse.py

To cross-build for a Windows / Python 3.12 target from a different OS:

    python make_wheelhouse.py --plat win_amd64 --pyver 3.12

This downloads every dependency pinned in requirements.txt as .whl files into
this folder, including:
  * the OCR stack:    rapidocr-onnxruntime, PyMuPDF  (RapidOCR's models ship
                      INSIDE its wheel, so OCR needs no Hugging Face download)
  * the dense stack:  fastembed, onnx, onnxruntime, lancedb
  * the GUI + core:   PySide6, httpx, certifi, truststore, keyring, etc.
  * PyInstaller       (so the .exe can also be built offline if desired)

WHAT IS NOT HERE
----------------
  * The dense/rerank model files are bundled separately in  models/  (already
    populated). RapidOCR's OCR models are inside its wheel.
  * Python itself is NOT here. The target machine must already have
    Python 3.10-3.12 installed.

NEXT STEP (on the target machine)
---------------------------------
  1. python -m venv .venv
  2. Activate the venv (.venv\Scripts\activate on Windows)
  3. python -m pip install --no-index --find-links wheelhouse -r requirements.txt
  4. python doctor.py     (verifies everything, including OCR)

  See DOCS.md for full details.
