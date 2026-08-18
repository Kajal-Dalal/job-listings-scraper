#!/bin/bash
# Development server startup script

set -e

echo "Starting Job Listings Scraper in development mode..."

# Check Python version
python_version=$(python3 --version 2>&1 | awk '{print $2}')
echo "Python version: $python_version"

# Install dependencies if needed
if [ ! -d ".venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv .venv
fi

# Activate venv
source .venv/bin/activate

# Install dependencies
echo "Installing dependencies..."
pip install -r requirements.txt -q

# Copy .env if not present
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo "Created .env from .env.example — update API_KEY before deploying!"
fi

# Seed database with sample data (optional)
if [ "$SEED_DB" = "true" ]; then
    echo "Seeding database..."
    python scripts/seed_db.py
fi

# Start the server
echo "Starting Uvicorn on http://0.0.0.0:8000"
echo "API docs: http://localhost:8000/docs"
uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload --log-level info
