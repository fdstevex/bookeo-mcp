"""Async Bookeo API client with rate limiting and pagination."""

import asyncio
import os
from datetime import datetime, timedelta
from typing import AsyncGenerator, Optional
from zoneinfo import ZoneInfo

import httpx
from dotenv import load_dotenv

load_dotenv()


class BookeoClient:
    """Async Bookeo API client with rate limiting and pagination."""

    BASE_URL = "https://api.bookeo.com/v2"

    def __init__(self):
        self.api_key = os.getenv("API_KEY")
        self.api_secret = os.getenv("API_SECRET")
        if not self.api_key or not self.api_secret:
            raise ValueError("API_KEY and API_SECRET must be set in .env")
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    async def _request(self, endpoint: str, params: Optional[dict] = None) -> dict:
        """Make authenticated request with rate limiting."""
        client = await self._get_client()
        params = params or {}
        params["apiKey"] = self.api_key
        params["secretKey"] = self.api_secret

        url = f"{self.BASE_URL}{endpoint}"

        while True:
            response = await client.get(url, params=params)

            if response.status_code == 429:
                retry_after = int(response.headers.get("Retry-After", 60))
                await asyncio.sleep(retry_after)
                continue

            response.raise_for_status()
            return response.json()

    async def get_booking(self, booking_number: str) -> dict:
        """Get a single booking by number."""
        return await self._request(
            f"/bookings/{booking_number}", {"expandCustomer": "true"}
        )

    async def get_booking_payments(self, booking_number: str) -> list:
        """Get payments for a specific booking."""
        data = await self._request(f"/bookings/{booking_number}/payments")
        return data.get("data", [])

    async def search_bookings(
        self,
        start_time: datetime,
        end_time: datetime,
        expand_customer: bool = True,
        include_canceled: bool = False,
    ) -> AsyncGenerator[dict, None]:
        """Search bookings with automatic pagination and 30-day chunking."""
        # Use local timezone (Pacific) for date interpretation, convert to UTC for API
        local_tz = ZoneInfo("America/Los_Angeles")
        utc_tz = ZoneInfo("UTC")

        current_start = start_time

        while current_start < end_time:
            chunk_end = min(current_start + timedelta(days=30), end_time)

            # Convert local dates to UTC for Bookeo API
            # Start at midnight local time, end at 23:59:59 local time
            start_local = current_start.replace(hour=0, minute=0, second=0, tzinfo=local_tz)
            end_local = chunk_end.replace(hour=23, minute=59, second=59, tzinfo=local_tz)

            start_utc = start_local.astimezone(utc_tz)
            end_utc = end_local.astimezone(utc_tz)

            page_token = None
            page_number = None

            while True:
                params = {
                    "startTime": start_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "endTime": end_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "itemsPerPage": 100,
                    "expandCustomer": str(expand_customer).lower(),
                    "includeCanceled": str(include_canceled).lower(),
                }

                if page_token:
                    params["pageNavigationToken"] = page_token
                    params["pageNumber"] = page_number

                data = await self._request("/bookings", params)

                for booking in data.get("data", []):
                    yield booking

                paging = data.get("info", {}).get("paging", {})
                if paging.get("nextPageURL"):
                    page_token = paging.get("pageNavigationToken")
                    page_number = paging.get("currentPage", 1) + 1
                else:
                    break

            current_start = chunk_end
            await asyncio.sleep(0.5)  # Rate limit courtesy

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None
