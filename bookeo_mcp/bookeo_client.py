"""Async Bookeo API client with rate limiting and pagination."""

import asyncio
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import AsyncGenerator, Optional
from zoneinfo import ZoneInfo

import httpx
from dotenv import load_dotenv

# Look next to the package first: clients like the Claude desktop app launch
# the server from an unrelated working directory, where the default search
# (which starts from the cwd in some launch modes) never finds the repo's .env.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")
load_dotenv()


class BookeoClient:
    """Async Bookeo API client with rate limiting and pagination."""

    BASE_URL = "https://api.bookeo.com/v2"

    def __init__(self):
        api_key = os.getenv("API_KEY")
        api_secret = os.getenv("API_SECRET")
        if not api_key or not api_secret:
            raise ValueError("API_KEY and API_SECRET must be set in .env")
        self.api_key = api_key
        self.api_secret = api_secret
        # Dates are interpreted in the business's timezone
        self.local_tz = ZoneInfo(os.getenv("BOOKEO_TIMEZONE", "America/Toronto"))
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    async def _request(self, endpoint: str, params: Optional[dict] = None) -> dict:
        """Make authenticated request with rate limiting."""
        client = await self._get_client()
        # Credentials go in headers, not the query string, so they never end up
        # in URLs quoted by httpx exceptions or request logs.
        headers = {
            "X-Bookeo-apiKey": self.api_key,
            "X-Bookeo-secretKey": self.api_secret,
        }

        url = f"{self.BASE_URL}{endpoint}"

        while True:
            response = await client.get(url, params=params, headers=headers)

            if response.status_code == 429:
                retry_after = int(response.headers.get("Retry-After", 60))
                await asyncio.sleep(retry_after)
                continue

            response.raise_for_status()
            return response.json()

    def today(self) -> datetime:
        """Midnight today in the business's timezone, as a naive datetime."""
        now = datetime.now(self.local_tz)
        return now.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)

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
        """Search bookings with automatic pagination and 30-day chunking.

        start_time and end_time are naive dates in the business's timezone;
        end_time is exclusive (pass the day after the last day wanted).
        """
        # Interpret dates in the business's timezone, convert to UTC for the API
        local_tz = self.local_tz
        utc_tz = ZoneInfo("UTC")

        current_start = start_time.replace(hour=0, minute=0, second=0, microsecond=0)
        end_time = end_time.replace(hour=0, minute=0, second=0, microsecond=0)

        while current_start < end_time:
            chunk_end = min(current_start + timedelta(days=30), end_time)

            # Each chunk runs from midnight local time up to the second before
            # the (exclusive) chunk end, so consecutive chunks never overlap.
            start_local = current_start.replace(tzinfo=local_tz)
            end_local = chunk_end.replace(tzinfo=local_tz) - timedelta(seconds=1)

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
