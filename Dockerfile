# ============================================================
# Multi-stage Dockerfile for Job Listings Scraper
# ============================================================

# --------------- Stage 1: Builder ---------------
FROM python:3.11-slim AS builder

WORKDIR /build

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libssl-dev \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy and install dependencies
COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip install --no-cache-dir --user -r requirements.txt

# --------------- Stage 2: Runtime ---------------
FROM python:3.11-slim AS runtime

# Create non-root user for security
RUN groupadd -r scraper && useradd -r -g scraper -d /app -s /sbin/nologin scraper

WORKDIR /app

# Install runtime dependencies only
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy Python packages from builder
COPY --from=builder /root/.local /home/scraper/.local

# Copy application source
COPY src/ ./src/
COPY .env.example .env.example

# Set ownership
RUN chown -R scraper:scraper /app

# Switch to non-root user
USER scraper

# Update PATH for local pip packages
ENV PATH=/home/scraper/.local/bin:$PATH
ENV PYTHONPATH=/app
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Expose API port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Default command
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
