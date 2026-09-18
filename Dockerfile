FROM python:3.12-slim

WORKDIR /app

# Copy project files
COPY pyproject.toml .
COPY bookeo_mcp/ bookeo_mcp/
# deploy.sh on the VM reads the compose file out of the image it is deploying,
# so the running stack always matches this commit
COPY ovm/docker-compose.yml ovm/docker-compose.yml

# Install the package
RUN pip install --no-cache-dir .

# Expose port for HTTP transport
EXPOSE 8000

# Run the MCP server with Streamable HTTP transport
# DNS rebinding protection is configured via ALLOWED_HOSTS env var
CMD ["bookeo-mcp", "--transport", "streamable-http", "--host", "0.0.0.0", "--port", "8000"]
