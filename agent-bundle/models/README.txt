This folder holds the pre-downloaded fastembed models for FULLY OFFLINE
dense retrieval and reranking. It ships EMPTY (only this README) because the
model weights (~55-60 MB) cannot be redistributed inside the source zip.

HOW TO POPULATE IT
------------------
On a machine WITH network access (e.g. your laptop), from the project root:

    pip install fastembed onnxruntime          # if not already installed
    python fetch_models.py

That downloads two models into this folder using fastembed's cache layout:

    models/models--qdrant--bge-small-en-v1.5-onnx-q/snapshots/<hash>/...
    models/models--Xenova--ms-marco-MiniLM-L-6-v2/snapshots/<hash>/...

WHAT HAPPENS NEXT
-----------------
The app (embeddings.py / reranker.py via model_bundle.py) auto-detects this
folder and loads the models with local_files_only=True - NO network calls at
runtime. If this folder is empty or missing, the app degrades gracefully to
lexical (BM25) retrieval with no code change.

To distribute to machines without network:
  * zip the project WITH this populated folder, OR
  * run 'python build.py' - build.py / TestingToolkit.spec bake this folder
    into the .exe automatically when it is present.

NOTE: do not set HF_HUB_OFFLINE=1 - it is not safe with fastembed (it can
bypass this local cache and try a Google Cloud Storage download instead).
The app uses local_files_only=True in code, which is the correct lever.
