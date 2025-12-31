FROM python:3.12-slim

WORKDIR /app

# Copy project files
COPY pyproject.toml .
COPY bookeo_mcp/ bookeo_mcp/

# Install the package
RUN pip install --no-cache-dir .

# Expose port for HTTP transport
EXPOSE 8000

# Disable DNS rebinding protection for containerized deployments
# (security handled by ingress/reverse proxy)
ENV FASTMCP_TRANSPORT_SECURITY__ENABLE_DNS_REBINDING_PROTECTION=false

# Run the MCP server with Streamable HTTP transport
CMD ["bookeo-mcp", "--transport", "streamable-http", "--host", "0.0.0.0", "--port", "8000"]
