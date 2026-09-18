# Bookeo MCP Server

An MCP (Model Context Protocol) server for looking up customer bookings and payment information from [Bookeo](https://www.bookeo.com/).

## Features

- Search bookings by customer name or email
- Look up specific bookings by booking number
- Search bookings within a date range
- Get payment details with manual vs Stripe payment detection

## Prerequisites

- Python 3.10+
- Bookeo API credentials (API Key and Secret Key)

## Installation

1. Clone the repository and create a virtual environment:

```bash
cd bookeo
python3 -m venv .venv
source .venv/bin/activate
```

2. Install the package:

```bash
pip install -e .
```

## Configuring with Claude Code

### Using `claude mcp add` (Recommended)

```bash
claude mcp add --transport stdio bookeo \
  -e API_KEY=your_bookeo_api_key \
  -e API_SECRET=your_bookeo_secret_key \
  -- /path/to/bookeo/.venv/bin/python -m bookeo_mcp.server
```

Replace `/path/to/bookeo` with the actual path to the project directory.

### Using `.mcp.json`

Alternatively, add to your `.mcp.json` file (project directory for project-specific, or `~/.claude/.mcp.json` for global):

```json
{
  "mcpServers": {
    "bookeo": {
      "type": "stdio",
      "command": "/path/to/bookeo/.venv/bin/python",
      "args": ["-m", "bookeo_mcp.server"],
      "cwd": "/path/to/bookeo",
      "env": {
        "API_KEY": "your_bookeo_api_key",
        "API_SECRET": "your_bookeo_secret_key"
      }
    }
  }
}
```

**Note:** If using `.mcp.json`, you can alternatively store credentials in a `.env` file in the project directory instead of in the config.

## Configuring with the Claude desktop app

Add the server to `~/Library/Application Support/Claude/claude_desktop_config.json`
(Settings → Developer → Edit Config), then quit and reopen the app:

```json
{
  "mcpServers": {
    "bookeo": {
      "command": "/path/to/bookeo/.venv/bin/python",
      "args": ["-m", "bookeo_mcp.server"]
    }
  }
}
```

The desktop app ignores `cwd` and starts the server from an unrelated
directory, so credentials are read from the `.env` next to the `bookeo_mcp`
package (the project directory for an editable install). Alternatively put
`API_KEY` and `API_SECRET` in an `env` block as above.

Dates passed to the search tools are interpreted in the business's timezone,
`America/Toronto` by default. Set `BOOKEO_TIMEZONE` to an IANA zone name to
change it.

## Available Tools

### search_bookings_by_customer

Search for bookings by customer name or email.

- `customer_name`: Full or partial customer name (case-insensitive)
- `customer_email`: Full or partial email address (case-insensitive)
- `days_back`: How many days back to search (default 90, max 365)

### get_booking

Look up a specific booking by its booking number.

- `booking_number`: The Bookeo booking number

### search_bookings_by_date

Find all bookings within a date range.

- `start_date`: Start date in YYYY-MM-DD format
- `end_date`: End date in YYYY-MM-DD format
- `include_canceled`: Whether to include canceled bookings (default false)

### get_booking_payments

Get payment details for a specific booking.

- `booking_number`: The Bookeo booking number

Returns payment breakdown including methods, amounts, and whether payments were manual or via Stripe.

## Running Standalone

### stdio Transport (default)

```bash
bookeo-mcp
```

### Streamable HTTP Transport

For network-accessible deployments, use the Streamable HTTP transport:

```bash
bookeo-mcp --transport streamable-http --host 0.0.0.0 --port 8000
```

Options:
- `--transport`: `stdio` (default) or `streamable-http`
- `--host`: Host to bind to (default: `127.0.0.1`)
- `--port`: Port to listen on (default: `8000`)

Environment variables for the HTTP transport:
- `AUTH_TOKEN`: if set, `/mcp` requires either `Authorization: Bearer <token>`
  or an OAuth access token obtained by signing in with it (see
  [Claude connectors](#adding-it-to-claude-as-a-connector))
- `PUBLIC_URL`: the URL clients reach the server at, advertised in the OAuth
  metadata (default `http://localhost:8000`)
- `ALLOWED_HOSTS`: comma-separated hosts for DNS rebinding protection, e.g.
  `ekbookeo.fallday.ca:*`; leave unset to disable it for local development

## Docker

### Using Pre-built Image

```bash
docker pull ghcr.io/fdstevex/bookeo-mcp:latest

docker run -p 8000:8000 \
  -e API_KEY=your_bookeo_api_key \
  -e API_SECRET=your_bookeo_secret_key \
  ghcr.io/fdstevex/bookeo-mcp:latest
```

### Building Locally

```bash
docker build -t bookeo-mcp .

docker run -p 8000:8000 \
  -e API_KEY=your_bookeo_api_key \
  -e API_SECRET=your_bookeo_secret_key \
  bookeo-mcp
```

The Docker image runs with Streamable HTTP transport on port 8000 by default.
The MCP endpoint is `/mcp`.

## Deployment

The server runs as a single container on the Oracle VM (`ovm.fallday.ca`,
arm64) at `https://ekbookeo.fallday.ca/mcp`, behind the shared Traefik on
`infra-network`.

Push to `main` and GitHub Actions builds a multi-arch (amd64 + arm64) image,
pushes it to `ghcr.io/fdstevex/bookeo-mcp:<sha>` (and `:latest`), then SSHes
to the VM with the sha. The deploy key is a forced command in the VM's
`~/.ssh/authorized_keys` that can only run `~/apps/bookeo/deploy.sh`, which
writes the tag to `.env`, pulls the image, takes `docker-compose.yml` out of it
and runs `docker compose up -d`. The compose file ships in the image so that it
deploys with the code while the key still carries nothing but a tag. CI runs
`tests/oauth_e2e.py` before building and, after deploying, checks that the
live server serves its OAuth metadata.

### VM prerequisites

- `~/apps/bookeo/` on the VM holding `docker-compose.yml`, `deploy.sh` and a
  `.env` with `API_KEY`, `API_SECRET`, `AUTH_TOKEN`, `ALLOWED_HOSTS` and
  `IMAGE_TAG`. Both files come from `ovm/` in this repo. CI keeps
  `docker-compose.yml` current; `deploy.sh` is the one file to `scp` over by
  hand when it changes, since it is what the deploy key is locked to.
- The public half of the CI deploy key in `~/.ssh/authorized_keys`, with
  `command="/home/ubuntu/apps/bookeo/deploy.sh"` and the usual restrictions.
  The private half is the `OVM_DEPLOY_KEY` repo secret.

### Manual deploy

```
ssh ubuntu@ovm.fallday.ca ~/apps/bookeo/deploy.sh <sha-or-latest>
```

### Adding it to Claude as a connector

Claude's custom connectors (claude.ai, the desktop app and the mobile apps)
only authenticate with OAuth; they cannot send a fixed header. The server is
its own single-user OAuth authorization server for this:

1. In Claude, Settings → Connectors → Add custom connector, URL
   `https://ekbookeo.fallday.ca/mcp`. Leave the OAuth client fields empty;
   Claude registers itself.
2. Connect. The browser opens the server's sign-in page; paste `AUTH_TOKEN`.

The connector belongs to the Claude account, so it then works on every
device. Nothing is stored server-side: client ids, codes and tokens are signed
with a key derived from `AUTH_TOKEN`, so they survive redeploys, and rotating
`AUTH_TOKEN` signs every connector out. `tests/oauth_e2e.py` exercises the
whole flow against a local server.

### Configuring Claude Code with the HTTP transport

To connect Claude Code to the deployed server:

```bash
claude mcp add --transport http bookeo https://ekbookeo.fallday.ca/mcp \
  --header "Authorization: Bearer <AUTH_TOKEN>"
```

Or in `.mcp.json`:

```json
{
  "mcpServers": {
    "bookeo": {
      "type": "http",
      "url": "https://ekbookeo.fallday.ca/mcp",
      "headers": {
        "Authorization": "Bearer <AUTH_TOKEN>"
      }
    }
  }
}
```
