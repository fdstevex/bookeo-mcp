FROM python:3.12-slim

WORKDIR /app

# Copy project files
COPY pyproject.toml .
COPY bookeo_mcp/ bookeo_mcp/

# Install the package
RUN pip install --no-cache-dir .

# Expose port for SSE transport (if used)
EXPOSE 8000

# Run the MCP server with SSE transport
CMD ["bookeo-mcp", "--transport", "sse", "--host", "0.0.0.0", "--port", "8000"]
