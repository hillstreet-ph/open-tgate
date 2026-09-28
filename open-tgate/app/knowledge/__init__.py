"""Downstream knowledge-base exporters for Open-TGate.

This package is a **separate, downstream consumer** of the entities the
Telegram sync worker has already mirrored into Supabase. It is deliberately
isolated from ``app.telegram`` — the sync path never imports anything here, so
adding or changing an export sink can never affect (or slow, or endanger) the
ban-safe Telegram synchronisation.
"""

from .notion_export import (
    NotionExporter,
    entity_to_notion_properties,
    external_key,
)

__all__ = ["NotionExporter", "entity_to_notion_properties", "external_key"]
