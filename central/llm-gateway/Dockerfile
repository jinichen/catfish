FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install system deps (minimal — litellm's deps are pure-ish)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency spec first for better layer caching
COPY pyproject.toml ./
COPY src/ ./src/

RUN pip install --upgrade pip \
    && pip install -e .

# Copy config last — changes more frequently
COPY config/ ./config/

EXPOSE 8000

CMD ["python", "-m", "catfish_gateway.app"]
