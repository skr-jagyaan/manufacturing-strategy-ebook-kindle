FROM python:3.11-slim

WORKDIR /app

# Install dependencies first (better Docker layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy all project files — worksheets are served from GCS at runtime, not baked in
COPY . .

# Cloud Run expects port 8080
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
