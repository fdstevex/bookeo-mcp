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

```bash
python -m bookeo_mcp.server
```

Or using the installed command:

```bash
bookeo-mcp
```
