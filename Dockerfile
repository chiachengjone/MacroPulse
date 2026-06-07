FROM --platform=linux/amd64 docker.elastic.co/mcp/elasticsearch AS elastic-mcp

FROM python:3.11-slim

# Bundle the Elastic MCP binary so it can run as a stdio subprocess inside
# the same container — no Docker-in-Docker, no separate Cloud Run service.
COPY --from=elastic-mcp /usr/local/bin/elasticsearch-core-mcp-server \
                         /usr/local/bin/elasticsearch-core-mcp-server

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .
COPY main.py .

EXPOSE 8080

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]