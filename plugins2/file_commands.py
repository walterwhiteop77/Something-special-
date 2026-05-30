"""
plugins2/file_commands.py
=========================
File-delivery handler loaded exclusively by Bot2.

All file delivery now goes through MongoDB:
  • Main bot creates a delivery record → gets delivery_id
  • URL only carries the opaque delivery_id — no file info exposed
  • Bot2 fetches file_id / file_ids from DB, delivers, then deletes the record

Supported /start payloads:
  get_{delivery_id}           — primary delivery path (DB-backed)
  notcopy_{uid}_{vid}_{did}   — post-verification single-file (did = delivery_id)
  sendall_{uid}_{vid}_{did}   — post-verification all-files   (did = delivery_id)
  file_{grp_id}_{file_id}     — legacy fallback for text-mode inline links
"""

import logging
import random
import asyncio
import string
import pytz

from datetime import datetime
from pyrogram import Client, filters, enums, StopPropagation
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from Script import script
from database.ia_filterdb import get_file_details
from database.users_chats_db import db
from info import (
    AUTH_CHANNELS, AUTH_REQ_CHANNELS, PROTECT_CONTENT, DELETE_TIME,
    CUSTOM_FILE_CAPTION, IS_VERIFY, TWO_VERIFY_GAP, THREE_VERIFY_GAP,
    STREAM_MODE, PREMIUM_STREAM_MODE, UPDATE_CHNL_LNK,
    IS_FILE_LIMIT, FILES_LIMIT, COVERX, FSUB_PICS,
    TUTORIAL, TUTORIAL_2, TUTORIAL_3, BIN_CHANNEL
)
from utils import (
    get_settings, is_subscribed, is_req_subscribed,
    get_size, get_shortlink, temp, get_readable_time, get_time,
    log_error, clean_filename
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

async def stream_buttons(user_id: int, file_id: str):
    if STREAM_MODE and not PREMIUM_STREAM_MODE:
        return [
            [InlineKeyboardButton('🚀 ꜰᴀꜱᴛ ᴅᴏᴡɴʟᴏᴀᴅ / ᴡᴀᴛᴄʜ ᴏɴʟɪɴᴇ 🖥️',
                                  callback_data=f'generate_stream_link:{file_id}')],
            [InlineKeyboardButton('ℹ️ ᴠɪᴇᴡ ᴀᴜᴅɪᴏ & ꜱᴜʙꜱ ɪɴꜰᴏ ℹ️',
                                  callback_data=f'extract_data:{file_id}')],
            [InlineKeyboardButton('📌 ᴊᴏɪɴ ᴜᴘᴅᴀᴛᴇꜱ ᴄʜᴀɴɴᴇʟ 📌', url=UPDATE_CHNL_LNK)],
        ]
    elif STREAM_MODE and PREMIUM_STREAM_MODE:
        if not await db.has_premium_access(user_id):
            return [
                [InlineKeyboardButton('🚀 ꜰᴀꜱᴛ ᴅᴏᴡɴʟᴏᴀᴅ / ᴡᴀᴛᴄʜ ᴏɴʟɪɴᴇ 🖥️',
                                      callback_data='prestream')],
                [InlineKeyboardButton('📌 ᴊᴏɪɴ ᴜᴘᴅᴀᴛᴇꜱ ᴄʜᴀɴɴᴇʟ 📌', url=UPDATE_CHNL_LNK)],
            ]
        else:
            return [
                [InlineKeyboardButton('🚀 ꜰᴀꜱᴛ ᴅᴏᴡɴʟᴏᴀᴅ / ᴡᴀᴛᴄʜ ᴏɴʟɪɴᴇ 🖥️',
                                      callback_data=f'generate_stream_link:{file_id}')],
                [InlineKeyboardButton('ℹ️ ᴠɪᴇᴡ ᴀᴜᴅɪᴏ & ꜱᴜʙꜱ ɪɴꜰᴏ ℹ️',
                                      callback_data=f'extract_data:{file_id}')],
                [InlineKeyboardButton('📌 ᴊᴏɪɴ ᴜᴘᴅᴀᴛᴇꜱ ᴄʜᴀɴɴᴇʟ 📌', url=UPDATE_CHNL_LNK)],
            ]
    else:
        return [[InlineKeyboardButton('📌 ᴊᴏɪɴ ᴜᴘᴅᴀᴛᴇꜱ ᴄʜᴀɴɴᴇʟ 📌', url=UPDATE_CHNL_LNK)]]


async def _send_single_file(client, user_id: int, file_id: str, grp_id: int, message):
    """Send one file and schedule auto-delete. Returns True on success."""
    settings = await get_settings(grp_id)
    files_ = await get_file_details(file_id)
    if files_:
        fi    = files_[0]
        title = clean_filename(fi.file_name)
        size  = get_size(fi.file_size)
        cover = fi.cover if fi.cover else None
        cap   = fi.caption
        DCAP  = settings.get('caption', CUSTOM_FILE_CAPTION)
        if DCAP:
            try:
                cap = DCAP.format(
                    file_name='' if title is None else title,
                    file_size='' if size is None else size,
                    file_caption='' if cap is None else cap,
                )
            except Exception:
                pass
        if cap is None:
            cap = clean_filename(fi.file_name)
    else:
        # bare file_id fallback
        fi, cover, cap = None, None, ''

    btn = await stream_buttons(user_id, file_id)
    stored_msg_id = getattr(fi, 'message_id', None) if fi else None
    if stored_msg_id:
        try:
            msg = await client.copy_message(
                chat_id=user_id,
                from_chat_id=BIN_CHANNEL,
                message_id=stored_msg_id,
                caption=cap,
                protect_content=settings.get('file_secure', PROTECT_CONTENT),
                reply_markup=InlineKeyboardMarkup(btn),
            )
        except Exception:
            msg = await client.send_cached_media(
                chat_id=user_id,
                file_id=file_id,
                cover=cover,
                caption=cap,
                protect_content=settings.get('file_secure', PROTECT_CONTENT),
                reply_markup=InlineKeyboardMarkup(btn),
            )
    else:
        msg = await client.send_cached_media(
            chat_id=user_id,
            file_id=file_id,
            cover=cover,
            caption=cap,
            protect_content=settings.get('file_secure', PROTECT_CONTENT),
            reply_markup=InlineKeyboardMarkup(btn),
        )
    k = await msg.reply(
        script.DEL_MSG.format(get_time(DELETE_TIME)),
        quote=True, parse_mode=enums.ParseMode.HTML,
    )
    await asyncio.sleep(DELETE_TIME)
    await msg.delete()
    await k.edit_text('<b>ʏᴏᴜʀ ᴠɪᴅᴇᴏ / ꜰɪʟᴇ ɪꜱ ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟʟʏ ᴅᴇʟᴇᴛᴇᴅ !!</b>')


async def _send_all_files(client, user_id: int, file_ids: list, grp_id: int, message):
    """Send every file in the list and schedule auto-delete."""
    settings = await get_settings(grp_id)
    sent_msgs = []
    for fid in file_ids:
        fdet = await get_file_details(fid)
        fi   = fdet[0] if fdet else None
        title = clean_filename(fi.file_name) if fi else ''
        size  = get_size(fi.file_size) if fi else ''
        cover = (fi.cover if fi.cover else None) if fi else None
        cap   = fi.caption if fi else ''
        DCAP  = settings.get('caption', CUSTOM_FILE_CAPTION)
        if DCAP and fi:
            try:
                cap = DCAP.format(
                    file_name='' if title is None else title,
                    file_size='' if size is None else size,
                    file_caption='' if cap is None else cap,
                )
            except Exception:
                pass
        if cap is None:
            cap = title or ''
        btn = await stream_buttons(user_id, fid)
        stored_msg_id = getattr(fi, 'message_id', None) if fi else None
        if stored_msg_id:
            try:
                msg = await client.copy_message(
                    chat_id=user_id,
                    from_chat_id=BIN_CHANNEL,
                    message_id=stored_msg_id,
                    caption=cap,
                    protect_content=settings.get('file_secure', PROTECT_CONTENT),
                    reply_markup=InlineKeyboardMarkup(btn),
                )
            except Exception:
                msg = await client.send_cached_media(
                    chat_id=user_id,
                    cover=cover,
                    file_id=fid,
                    caption=cap,
                    protect_content=settings.get('file_secure', PROTECT_CONTENT),
                    reply_markup=InlineKeyboardMarkup(btn),
                )
        else:
            msg = await client.send_cached_media(
                chat_id=user_id,
                cover=cover,
                file_id=fid,
                caption=cap,
                protect_content=settings.get('file_secure', PROTECT_CONTENT),
                reply_markup=InlineKeyboardMarkup(btn),
            )
        sent_msgs.append(msg)
    k = await client.send_message(
        chat_id=user_id,
        text=script.DEL_MSG.format(get_time(DELETE_TIME)),
        parse_mode=enums.ParseMode.HTML,
    )
    await asyncio.sleep(DELETE_TIME)
    for x in sent_msgs:
        await x.delete()
    await k.edit_text(
        '<b>ʏᴏᴜʀ ᴀʟʟ ᴠɪᴅᴇᴏꜱ/ꜰɪʟᴇꜱ ᴀʀᴇ ᴅᴇʟᴇᴛᴇᴅ ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟʟʏ !\nᴋɪɴᴅʟʏ ꜱᴇᴀʀᴄʜ ᴀɢᴀɪɴ</b>'
    )


async def _fsub_check(client, user_id: int, grp_id: int, payload: str, message):
    """Return True if user passes force-subscribe check, False otherwise."""
    if await db.has_premium_access(user_id):
        return True
    try:
        btn = []
        settings = await get_settings(grp_id)
        fsub_channels = list(dict.fromkeys(
            (settings.get('fsub', []) if settings else []) + AUTH_CHANNELS
        ))
        if fsub_channels:
            btn += await is_subscribed(client, user_id, fsub_channels)
        if AUTH_REQ_CHANNELS:
            btn += await is_req_subscribed(client, user_id, AUTH_REQ_CHANNELS)
        if btn:
            if '_' in payload:
                kk, rest = payload.split('_', 1)
                btn.append([InlineKeyboardButton(
                    '♻️ ᴛʀʏ ᴀɢᴀɪɴ ♻️',
                    callback_data=f'checksub#{kk}#{rest}',
                )])
            photo = random.choice(FSUB_PICS) if FSUB_PICS else \
                'https://graph.org/file/7478ff3eac37f4329c3d8.jpg'
            await message.reply_photo(
                photo=photo,
                caption=script.FORCESUB_TXT.format(message.from_user.mention),
                reply_markup=InlineKeyboardMarkup(btn),
                parse_mode=enums.ParseMode.HTML,
            )
            return False
    except Exception as e:
        await log_error(client, f'❗️ Bot2 ForceSub Error:\n\n{repr(e)}')
    return True


async def _verify_check(client, user_id: int, grp_id: int, delivery_id: str,
                        is_allfiles: bool, message, settings):
    """
    Run the verification check.
    Returns True if verified (or verification disabled/bypassed).
    Returns False and sends the shortener message if verification is required.
    """
    if await db.has_premium_access(user_id):
        return True
    try:
        user_verified       = await db.is_user_verified(user_id)
        is_second_shortener = await db.use_second_shortener(
            user_id, settings.get('verify_time', TWO_VERIFY_GAP)
        )
        is_third_shortener  = await db.use_third_shortener(
            user_id, settings.get('third_verify_time', THREE_VERIFY_GAP)
        )
        if not settings.get('is_verify', IS_VERIFY):
            return True
        if user_verified and not is_second_shortener and not is_third_shortener:
            return True

        verify_id = ''.join(random.choices(string.ascii_uppercase + string.digits, k=7))
        await db.create_verify_id(user_id, verify_id)
        temp.VERIFICATIONS[user_id] = grp_id

        _bot2 = temp.U_NAME2 or temp.U_NAME
        payload_prefix = 'sendall' if is_allfiles else 'notcopy'
        shortener_url = (
            f'https://telegram.me/{_bot2}'
            f'?start={payload_prefix}_{user_id}_{verify_id}_{delivery_id}'
        )
        verify = await get_shortlink(
            shortener_url, grp_id, is_second_shortener, is_third_shortener
        )
        if is_third_shortener:
            howtodownload = settings.get('tutorial_3', TUTORIAL_3)
        else:
            howtodownload = settings.get('tutorial_2', TUTORIAL_2) \
                if is_second_shortener else settings.get('tutorial', TUTORIAL)

        if await db.user_verified(user_id):
            msg = script.THIRDT_VERIFICATION_TEXT
        else:
            msg = script.SECOND_VERIFICATION_TEXT if is_second_shortener \
                else script.VERIFICATION_TEXT

        n = await message.reply_text(
            text=msg.format(message.from_user.mention),
            protect_content=True,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton('♻️ ᴄʟɪᴄᴋ ʜᴇʀᴇ ᴛᴏ ᴠᴇʀɪꜰʏ ♻️', url=verify)],
                [InlineKeyboardButton('⁉️ ʜᴏᴡ ᴛᴏ ᴠᴇʀɪꜰʏ ⁉️', url=howtodownload)],
            ]),
            parse_mode=enums.ParseMode.HTML,
        )
        await asyncio.sleep(300)
        await n.delete()
        await message.delete()
        return False
    except Exception as e:
        logger.exception(f'Bot2 verify check error: {e}')
        return True  # let through on error


# ─────────────────────────────────────────────────────────────────────────────
# /start handler
# ─────────────────────────────────────────────────────────────────────────────

@Client.on_message(filters.command('start') & filters.private & filters.incoming)
async def start_file_delivery(client, message):
    """Bot2's /start handler — DB-backed file delivery."""
    sticker = None
    try:
        stick_id = 'CAACAgUAAxkBAAEQJmJpViid_0yscWKPfh3RMCY8pIkmXwACMAcAAqzbsFexyKU6FPQAAjgE'
        try:
            sticker = await message.reply_sticker(sticker=stick_id)
        except Exception:
            pass

        if len(message.command) != 2:
            return

        payload = message.command[1]
        user_id = message.from_user.id

        # ── POST-VERIFICATION CALLBACKS ────────────────────────────────────
        # payload: notcopy_{uid}_{verify_id}_{delivery_id}
        #       or sendall_{uid}_{verify_id}_{delivery_id}
        if payload.startswith(('notcopy', 'sendall')):
            try:
                _, userid, verify_id, delivery_id = payload.split('_', 3)
            except ValueError:
                return await message.reply(script.LINK_EXPIRED_TXT)

            uid     = int(userid)
            grp_id  = temp.VERIFICATIONS.get(uid, 0)
            settings = await get_settings(grp_id)

            verify_id_info = await db.get_verify_id_info(uid, verify_id)
            if not verify_id_info or verify_id_info['verified']:
                return await message.reply(script.LINK_EXPIRED_TXT)

            ist_tz = pytz.timezone('Asia/Kolkata')
            if await db.user_verified(uid):
                key = 'third_time_verified'
            elif await db.is_user_verified(uid):
                key = 'second_time_verified'
            else:
                key = 'last_verified'

            await db.update_notcopy_user(uid, {key: datetime.now(tz=ist_tz)})
            await db.update_verify_id_info(uid, verify_id, {'verified': True})

            num_map = {'third_time_verified': 3, 'second_time_verified': 2, 'last_verified': 1}
            msg_map = {
                'third_time_verified': script.THIRDT_VERIFY_COMPLETE_TEXT,
                'second_time_verified': script.SECOND_VERIFY_COMPLETE_TEXT,
                'last_verified': script.VERIFY_COMPLETE_TEXT,
            }
            num = num_map[key]
            msg = msg_map[key]

            _bot2 = temp.U_NAME2 or temp.U_NAME
            get_file_url = f'https://telegram.me/{_bot2}?start=get_{delivery_id}'

            try:
                await client.send_message(
                    settings['log'],
                    script.VERIFIED_LOG_TEXT.format(
                        message.from_user.mention, uid,
                        datetime.now(pytz.timezone('Asia/Kolkata')).strftime('%d %B %Y'), num
                    ),
                )
            except Exception:
                pass
            dlt = await message.reply_photo(
                photo=(settings.get('verify_img')
                       or 'https://graph.org/file/7478ff3eac37f4329c3d8.jpg'),
                caption=msg.format(
                    message.from_user.mention, get_readable_time(TWO_VERIFY_GAP)
                ),
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton('✅ ᴄʟɪᴄᴋ ʜᴇʀᴇ ᴛᴏ ɢᴇᴛ ꜰɪʟᴇ ✅', url=get_file_url),
                ]]),
                parse_mode=enums.ParseMode.HTML,
            )
            if sticker:
                await sticker.delete()
            await asyncio.sleep(300)
            await dlt.delete()
            return

        # ── DB-BACKED DELIVERY: get_{delivery_id} ─────────────────────────
        if payload.startswith('get_'):
            delivery_id = payload[4:]
            record = await db.get_file_delivery(delivery_id)
            if not record:
                if sticker:
                    await sticker.delete()
                return await message.reply(
                    '<b>⚠️ ʟɪɴᴋ ᴇxᴘɪʀᴇᴅ ᴏʀ ɪɴᴠᴀʟɪᴅ. ᴘʟᴇᴀꜱᴇ ᴄʟɪᴄᴋ ᴛʜᴇ ꜰɪʟᴇ ʙᴜᴛᴛᴏɴ ᴀɢᴀɪɴ.</b>',
                    parse_mode=enums.ParseMode.HTML,
                )

            grp_id   = record['grp_id']
            rec_type = record['type']
            settings = await get_settings(grp_id)

            # FSub check
            if not await _fsub_check(client, user_id, grp_id, payload, message):
                if sticker:
                    await sticker.delete()
                return

            # Verification check
            is_allfiles = rec_type == 'allfiles'
            if not await _verify_check(
                client, user_id, grp_id, delivery_id, is_allfiles, message, settings
            ):
                if sticker:
                    await sticker.delete()
                return

            # File limit check (single files only, non-premium)
            if IS_FILE_LIMIT and FILES_LIMIT > 0 and not is_allfiles \
                    and not await db.has_premium_access(user_id):
                current_count = await db.get_file_limit(user_id)
                if current_count >= FILES_LIMIT:
                    if sticker:
                        await sticker.delete()
                    return await message.reply_text(
                        f'<b>⚠️ ʏᴏᴜ ʜᴀᴠᴇ ʀᴇᴀᴄʜᴇᴅ ʏᴏᴜʀ ꜰʀᴇᴇ ꜰɪʟᴇ ʟɪᴍɪᴛ!\n\n'
                        f'📊 ᴜsᴇᴅ: {current_count}/{FILES_LIMIT} ꜰʀᴇᴇ ꜰɪʟᴇs\n\n'
                        f'💎 ᴜᴘɢʀᴀᴅᴇ ᴛᴏ ᴘʀᴇᴍɪᴜᴍ ꜰᴏʀ ᴜɴʟɪᴍɪᴛᴇᴅ ꜰɪʟᴇs!</b>',
                        parse_mode=enums.ParseMode.HTML,
                    )
                await db.increment_file_limit(user_id)

            # Deliver
            if sticker:
                await sticker.delete()
            if is_allfiles:
                file_ids = record.get('file_ids', [])
                await _send_all_files(client, user_id, file_ids, grp_id, message)
            else:
                await _send_single_file(
                    client, user_id, record['file_id'], grp_id, message
                )

            # Consume the record so it can't be replayed
            await db.del_file_delivery(delivery_id)
            return

        # ── LEGACY FALLBACK: file_{grp_id}_{file_id} (text-mode links) ────
        if payload.startswith('file_'):
            try:
                _, grp_id_str, file_id = payload.split('_', 2)
                grp_id = int(grp_id_str)
            except Exception:
                grp_id  = 0
                file_id = payload
            settings = await get_settings(grp_id)

            if not await _fsub_check(client, user_id, grp_id, payload, message):
                if sticker:
                    await sticker.delete()
                return

            # Create a delivery record on-the-fly so verification loop works
            delivery_id = await db.create_file_delivery(
                user_id=user_id, grp_id=grp_id, type_='file', file_id=file_id,
            )
            if not await _verify_check(
                client, user_id, grp_id, delivery_id, False, message, settings
            ):
                if sticker:
                    await sticker.delete()
                return

            if IS_FILE_LIMIT and FILES_LIMIT > 0 \
                    and not await db.has_premium_access(user_id):
                current_count = await db.get_file_limit(user_id)
                if current_count >= FILES_LIMIT:
                    if sticker:
                        await sticker.delete()
                    return await message.reply_text(
                        f'<b>⚠️ ʏᴏᴜ ʜᴀᴠᴇ ʀᴇᴀᴄʜᴇᴅ ʏᴏᴜʀ ꜰʀᴇᴇ ꜰɪʟᴇ ʟɪᴍɪᴛ!\n\n'
                        f'📊 ᴜsᴇᴅ: {current_count}/{FILES_LIMIT} ꜰʀᴇᴇ ꜰɪʟᴇs\n\n'
                        f'💎 ᴜᴘɢʀᴀᴅᴇ ᴛᴏ ᴘʀᴇᴍɪᴜᴍ ꜰᴏʀ ᴜɴʟɪᴍɪᴛᴇᴅ ꜰɪʟᴇs!</b>',
                        parse_mode=enums.ParseMode.HTML,
                    )
                await db.increment_file_limit(user_id)

            if sticker:
                await sticker.delete()
            await _send_single_file(client, user_id, file_id, grp_id, message)
            await db.del_file_delivery(delivery_id)
            return

    except StopPropagation:
        raise
    except Exception as e:
        logger.exception(f'Bot2 /start error: {e}')
    finally:
        if sticker:
            try:
                await sticker.delete()
            except Exception:
                pass
