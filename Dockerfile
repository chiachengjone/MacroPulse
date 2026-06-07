FROM --platform=linux/amd64 docker.elastic.co/mcp/elasticsearch AS elastic-mcp

FROM python:3.11-slim

# Unbuffered stdout/stderr ensures logs appear in Cloud Run without delay.
ENV PYTHONUNBUFFERED=1

# Bundle the official Elastic MCP binary directly into the image.
# It runs as a stdio subprocess at runtime — no Docker-in-Docker, no
# separate sidecar service, no extra Cloud Run service required.
COPY --from=elastic-mcp /usr/local/bin/elasticsearch-core-mcp-server \
                         /usr/local/bin/elasticsearch-core-mcp-server

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application source
COPY app.py main.py ui.py ./

EXPOSE 8080

# Serve the FastAPI backend. The Streamlit UI (ui.py) is run separately:
#   streamlit run ui.py
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]