# Léa sur Hugging Face Spaces (CPU gratuit, Linux x86_64).
# Écoute et réflexion chez Groq ; ici tournent seulement le détecteur de voix
# (Silero) et la synthèse vocale (Piper), très légers.
FROM python:3.12-slim

# libgl/libglib : requis par opencv, importé par le transport WebRTC de Pipecat
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

WORKDIR /app
COPY pyproject.toml uv.lock README.md .python-version ./
COPY src ./src

ENV UV_PYTHON_DOWNLOADS=never
RUN uv sync --frozen --no-dev --extra voice --extra google

# Voix française de Piper
RUN mkdir -p models/piper && cd models/piper \
    && for f in fr_FR-siwis-medium.onnx fr_FR-siwis-medium.onnx.json; do \
         curl -fL --retry 5 -o "$f" "https://huggingface.co/rhasspy/piper-voices/resolve/main/fr/fr_FR/siwis/medium/$f"; \
       done

# Découpage des phrases avant la synthèse vocale
ENV NLTK_DATA=/app/nltk_data
RUN .venv/bin/python -c "import nltk; nltk.download('punkt_tab', download_dir='/app/nltk_data')"

# Les Spaces exécutent le conteneur avec l'utilisateur 1000 : tout doit être inscriptible.
ENV NUMBA_CACHE_DIR=/tmp/numba HOME=/tmp PYTHONUNBUFFERED=1
RUN mkdir -p data && chown -R 1000:1000 /app/data
USER 1000

EXPOSE 7860
CMD [".venv/bin/lea-voice", "--http", "--host", "0.0.0.0", "--port", "7860"]
