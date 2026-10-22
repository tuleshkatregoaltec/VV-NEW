#!/usr/bin/env python3
"""Replay an original owner file atomically, preserving import/list/claim/note IDs."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from fastapi import UploadFile
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from app.clickhouse.client import close_clickhouse
from app.crm import import_repository
from app.crm.imports import process_contact_import
from app.postgres import close_postgres, get_postgres_engine


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--organization-id", required=True)
    parser.add_argument("--import-id", type=int, required=True)
    parser.add_argument("--file", type=Path, required=True)
    args = parser.parse_args()
    factory = async_sessionmaker(get_postgres_engine(), class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as db:
            record = await import_repository.get_import_for_org(
                db, args.import_id, args.organization_id
            )
            if record is None:
                raise SystemExit("Import not found in the specified organization.")
            with args.file.open("rb") as source:
                response = await process_contact_import(
                    db,
                    upload=UploadFile(file=source, filename=args.file.name),
                    name=record.name,
                    user_id=record.created_by_user_id,
                    organization_id=args.organization_id,
                    reprocess_import_id=args.import_id,
                )
            print(json.dumps({"import_id": response.id, "summary": response.summary.model_dump()}))
    finally:
        await close_clickhouse()
        await close_postgres()


if __name__ == "__main__":
    asyncio.run(main())
