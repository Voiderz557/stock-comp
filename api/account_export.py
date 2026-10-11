"""Read-only export of rows visible under the caller's Supabase RLS policy.

This intentionally does not use a service-role key or mutate any account row.
The export is not a transactionally frozen snapshot: concurrent account writes
may be reflected in different tables at different moments.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import httpx
from fastapi import HTTPException


_TABLE_ORDER = {
    "profiles": "user_id",
    "competition_accounts": "id",
    "positions": "id",
    "plans": "id",
    "recommendations": "id",
    "recommendation_events": "id",
    "paper_actions": "id",
    "portfolio_snapshots": "id",
    "import_jobs": "id",
    "audit_events": "id",
    "planning_profiles": "id",
}
_PAGE_SIZE = 500
_MAX_ROWS_PER_TABLE = 100_000


async def export_owned_account_data(access_token: str) -> dict:
    """Fetch every allowed table page using the user's JWT and RLS.

    A missing table, denied request, malformed page, or excessive result fails
    the entire export; an incomplete archive is never returned as complete.
    """
    supabase_url = (os.getenv("STOCK_COMP_SUPABASE_URL") or "").strip().rstrip("/")
    publishable_key = (os.getenv("STOCK_COMP_SUPABASE_PUBLISHABLE_KEY") or "").strip()
    if not supabase_url or not publishable_key:
        raise HTTPException(status_code=503, detail="Authenticated account storage is not configured yet.")
    headers = {"apikey": publishable_key, "Authorization": f"Bearer {access_token}"}
    tables: dict[str, list[dict]] = {}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
            for table_name, order_key in _TABLE_ORDER.items():
                rows: list[dict] = []
                while True:
                    response = await client.get(
                        f"{supabase_url}/rest/v1/{table_name}",
                        headers=headers,
                        params={
                            "select": "*",
                            "order": f"{order_key}.asc",
                            "limit": str(_PAGE_SIZE),
                            "offset": str(len(rows)),
                        },
                    )
                    if response.status_code in {401, 403}:
                        raise HTTPException(status_code=403, detail="Account export access was denied.")
                    if response.status_code == 404:
                        raise HTTPException(status_code=503, detail="Account export requires all cloud migrations to be installed.")
                    if response.is_error:
                        raise HTTPException(status_code=502, detail="Account export storage returned an error.")
                    try:
                        page = response.json()
                    except ValueError as exc:
                        raise HTTPException(status_code=502, detail="Account export storage returned invalid data.") from exc
                    if not isinstance(page, list) or any(not isinstance(row, dict) for row in page):
                        raise HTTPException(status_code=502, detail="Account export storage returned invalid data.")
                    if len(rows) + len(page) > _MAX_ROWS_PER_TABLE:
                        raise HTTPException(status_code=413, detail="Account export exceeds the supported size. Contact the operator for a database export.")
                    rows.extend({key: value for key, value in row.items() if key != "user_id"} for row in page)
                    if len(page) < _PAGE_SIZE:
                        break
                tables[table_name] = rows
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail="Account export storage timed out.") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Account export storage is unavailable.") from exc

    return {
        "schema_version": 1,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "scope": "All rows visible to the signed-in user in the listed application tables.",
        "consistency": "Read-only sequential export; concurrent account edits may span tables. No auth-provider profile or credential is included.",
        "tables": tables,
        "counts": {table_name: len(rows) for table_name, rows in tables.items()},
    }
