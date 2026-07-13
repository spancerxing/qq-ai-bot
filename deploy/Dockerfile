FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir -e .

# Copy source
COPY . .

# Expose port
EXPOSE 8000

# Run
CMD ["python", "main.py"]
