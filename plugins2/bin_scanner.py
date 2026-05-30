"""
BIN_CHANNEL scanner — runs once at Bot2 startup in background.

Scans BIN_CHANNEL history and patches each DB record with the
corresponding BIN_CHANNEL message_id so that copy_message can be used
for file delivery (Bot2 as the visible sender, no MEDIA_EMPTY).

unpack_new_file_id produces the same packed _id regardless of which
bot session indexed the file, so this cross-references correctly.
"""
import asyncio
import logging

from pyrogram import enums

from database.ia_filterdb import (
    Media, Media2, _patch_message_id, unpack_new_file_id,
)
from info import BIN_CHANNEL, MULTIPLE_DB

logger = logging.getLogger(__name__)

_SUPPORTED_MEDIA = (
    enums.MessageMediaType.DOCUMENT,
    enums.MessageMediaType.VIDEO,
    enums.MessageMediaType.AUDIO,
)


async def run_bin_channel_scan(client):
    """Background task: populate message_id for all DB records via BIN_CHANNEL scan."""
    logger.info("[BinScanner] Starting BIN_CHANNEL scan to populate message_ids …")
    total = 0
    patched = 0
    try:
        async for message in client.get_chat_history(BIN_CHANNEL):
            if not message.media or message.media not in _SUPPORTED_MEDIA:
                continue
            media = getattr(message, message.media.value, None)
            if not media or not getattr(media, "file_id", None):
                continue
            total += 1
            try:
                packed_id, _ = unpack_new_file_id(media.file_id)
            except Exception:
                continue

            col = Media.collection
            if MULTIPLE_DB:
                exists_primary = await Media.count_documents(
                    {"file_id": packed_id}, limit=1
                )
                if not exists_primary:
                    col = Media2.collection

            await _patch_message_id(col, packed_id, message.id)
            patched += 1

            if total % 500 == 0:
                logger.info(
                    "[BinScanner] Scanned %d messages, patched %d records so far …",
                    total, patched,
                )
            await asyncio.sleep(0)  # yield to event loop between messages

    except Exception as e:
        logger.error("[BinScanner] Scan error: %s", e, exc_info=True)
        return

    logger.info(
        "[BinScanner] Scan complete. Scanned %d messages, patched %d DB records.",
        total, patched,
    )
