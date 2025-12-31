"""Bookeo MCP Server - Look up customer bookings and payment information."""

import argparse
from datetime import datetime, timedelta
from typing import Optional

from mcp.server.fastmcp import FastMCP

from .bookeo_client import BookeoClient

mcp = FastMCP("Bookeo")


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments for transport configuration."""
    parser = argparse.ArgumentParser(description="Bookeo MCP Server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse"],
        default="stdio",
        help="Transport type (default: stdio)",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host for SSE transport (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port for SSE transport (default: 8000)",
    )
    return parser.parse_args()

_client: Optional[BookeoClient] = None


def get_client() -> BookeoClient:
    global _client
    if _client is None:
        _client = BookeoClient()
    return _client


def format_customer(booking: dict) -> dict:
    """Extract customer info from booking."""
    customer = booking.get("customer", {})
    phone_numbers = customer.get("phoneNumbers", [])
    return {
        "name": f"{customer.get('firstName', '')} {customer.get('lastName', '')}".strip(),
        "email": customer.get("emailAddress", ""),
        "phone": phone_numbers[0].get("number", "") if phone_numbers else "",
    }


def format_price(booking: dict) -> dict:
    """Extract price info from booking."""
    price = booking.get("price", {})
    return {
        "total_gross": price.get("totalGross", {}),
        "total_paid": price.get("totalPaid", {}),
        "balance_due": price.get("balanceDue", {}),
    }


def format_participants(booking: dict) -> int:
    """Extract participant count."""
    participants = booking.get("participants", {})
    numbers = participants.get("numbers", [])
    return sum(p.get("number", 0) for p in numbers)


def analyze_payment(payment: dict) -> dict:
    """Analyze a single payment for method and gateway."""
    gateway = payment.get("gatewayName", "")
    return {
        "amount": payment.get("amount", {}),
        "method": payment.get("paymentMethod", "unknown"),
        "gateway": gateway if gateway else "manual",
        "is_manual": not bool(gateway),
        "reason": payment.get("reason", ""),
        "agent": payment.get("agent", ""),
        "received_time": payment.get("receivedTime", ""),
    }


@mcp.tool()
async def search_bookings_by_customer(
    customer_name: str = "", customer_email: str = "", days_back: int = 90
) -> list[dict]:
    """
    Search for bookings by customer name or email.

    Args:
        customer_name: Full or partial customer name to search for (case-insensitive)
        customer_email: Full or partial email address to search for (case-insensitive)
        days_back: How many days back to search (default 90, max 365)

    Returns:
        List of matching bookings with customer info, dates, and product details
    """
    if not customer_name and not customer_email:
        return [{"error": "Must provide either customer_name or customer_email"}]

    days_back = min(days_back, 365)

    client = get_client()
    end_time = datetime.now()
    start_time = end_time - timedelta(days=days_back)

    results = []
    name_lower = customer_name.lower() if customer_name else ""
    email_lower = customer_email.lower() if customer_email else ""

    async for booking in client.search_bookings(start_time, end_time):
        customer = format_customer(booking)

        name_match = name_lower and name_lower in customer["name"].lower()
        email_match = email_lower and email_lower in customer["email"].lower()

        if name_match or email_match:
            results.append(
                {
                    "booking_number": booking.get("bookingNumber"),
                    "start_time": booking.get("startTime"),
                    "product_name": booking.get("productName"),
                    "customer": customer,
                    "participants": format_participants(booking),
                    "price": format_price(booking),
                }
            )

    return results


@mcp.tool()
async def get_booking(booking_number: str) -> dict:
    """
    Look up a specific booking by its booking number.

    Args:
        booking_number: The Bookeo booking number (e.g., "123456789")

    Returns:
        Complete booking details including customer, pricing, and product info
    """
    client = get_client()

    try:
        booking = await client.get_booking(booking_number)

        return {
            "booking_number": booking.get("bookingNumber"),
            "start_time": booking.get("startTime"),
            "end_time": booking.get("endTime"),
            "product_name": booking.get("productName"),
            "product_id": booking.get("productId"),
            "customer": format_customer(booking),
            "participants": format_participants(booking),
            "price": format_price(booking),
            "price_adjustments": booking.get("priceAdjustments", []),
            "creation_time": booking.get("creationTime"),
            "source": booking.get("source", {}),
        }
    except Exception as e:
        return {"error": f"Booking not found or API error: {str(e)}"}


@mcp.tool()
async def search_bookings_by_date(
    start_date: str, end_date: str, include_canceled: bool = False
) -> list[dict]:
    """
    Find all bookings within a date range.

    Args:
        start_date: Start date in YYYY-MM-DD format
        end_date: End date in YYYY-MM-DD format
        include_canceled: Whether to include canceled bookings

    Returns:
        List of bookings with summary info
    """
    try:
        start_time = datetime.strptime(start_date, "%Y-%m-%d")
        # Add 1 day to make end_time exclusive (so Dec 27-27 searches Dec 27's full day)
        end_time = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
    except ValueError:
        return [{"error": "Invalid date format. Use YYYY-MM-DD"}]

    if (end_time - start_time).days > 366:  # 366 because end_time has +1 day added
        return [{"error": "Date range cannot exceed 365 days"}]

    client = get_client()
    results = []

    async for booking in client.search_bookings(
        start_time, end_time, include_canceled=include_canceled
    ):
        results.append(
            {
                "booking_number": booking.get("bookingNumber"),
                "start_time": booking.get("startTime"),
                "product_name": booking.get("productName"),
                "customer": format_customer(booking),
                "participants": format_participants(booking),
                "price": format_price(booking),
            }
        )

    return results


@mcp.tool()
async def get_booking_payments(booking_number: str) -> dict:
    """
    Get payment details for a specific booking.

    Args:
        booking_number: The Bookeo booking number

    Returns:
        Payment breakdown including methods, amounts, and manual vs Stripe detection
    """
    client = get_client()

    try:
        payments = await client.get_booking_payments(booking_number)

        analyzed_payments = [analyze_payment(p) for p in payments]

        total_paid = sum(
            float(p["amount"].get("amount", 0) or 0) for p in analyzed_payments
        )

        has_manual = any(p["is_manual"] for p in analyzed_payments)
        has_stripe = any(
            "stripe" in p["gateway"].lower()
            for p in analyzed_payments
            if p["gateway"]
        )

        payment_methods = list(set(p["method"] for p in analyzed_payments))

        return {
            "booking_number": booking_number,
            "payment_count": len(payments),
            "total_paid": total_paid,
            "currency": (
                analyzed_payments[0]["amount"].get("currency", "CAD")
                if analyzed_payments
                else "CAD"
            ),
            "has_manual_payment": has_manual,
            "has_stripe_payment": has_stripe,
            "payment_methods": payment_methods,
            "payments": analyzed_payments,
        }
    except Exception as e:
        return {"error": f"Could not fetch payments: {str(e)}"}


def main():
    """Run the MCP server with the configured transport."""
    args = parse_args()

    if args.transport == "sse":
        mcp.run(transport="sse", host=args.host, port=args.port)
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
