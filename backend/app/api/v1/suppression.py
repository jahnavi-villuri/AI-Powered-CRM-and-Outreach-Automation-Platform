"""
Suppression & Do-Not-Contact Management API.
"""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from backend.app.core.security import require_roles
from backend.app.services import suppression_service

router = APIRouter(prefix="/suppression", tags=["Suppression"])

READ_ROLES = ["admin", "campaign_manager", "researcher"]
WRITE_ROLES = ["admin", "campaign_manager"]


class CreateSuppressionEntry(BaseModel):
    value: str = Field(min_length=1, max_length=255)
    kind: Literal["email", "domain"]
    reason: str = Field(min_length=1, max_length=2000)
    source: Literal["manual", "bounce", "unsubscribe", "admin"] = "manual"


class RemoveSuppressionEntry(BaseModel):
    justification: str = Field(min_length=1, max_length=2000)


@router.get("")
def list_suppressions(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    current_user: dict = Depends(require_roles(READ_ROLES)),
):
    """List suppression entries (paginated)."""
    return suppression_service.list_suppressions(limit=limit, offset=offset)


@router.post("", status_code=status.HTTP_201_CREATED)
def add_suppression(
    payload: CreateSuppressionEntry,
    current_user: dict = Depends(require_roles(WRITE_ROLES)),
):
    """Add an email-address or whole-domain suppression entry."""
    try:
        return suppression_service.create_suppression(
            value=payload.value,
            kind=payload.kind,
            reason=payload.reason,
            source=payload.source,
            created_by=current_user["id"],
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


@router.delete("/{entry_id}")
def delete_suppression(
    entry_id: UUID,
    payload: RemoveSuppressionEntry,
    current_user: dict = Depends(require_roles(["admin"])),
):
    """Remove an entry; admins must supply a justification and removal is audited."""
    try:
        return suppression_service.remove_suppression(
            entry_id=entry_id,
            justification=payload.justification,
            actor=current_user["id"],
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
