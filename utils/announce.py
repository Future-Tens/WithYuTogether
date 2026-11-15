"""發公告到伺服器用 /設定公告 指定的頻道。"""
import discord

from utils.db_manager import DatabaseManager


async def send_announcement(guild, text, fallback=None, **kwargs):
    """沒設定公告頻道（或頻道不見了）時改發到 fallback；fallback 也沒有就不發。"""
    channel = None
    if guild is not None:
        channel_id = DatabaseManager().get_announce_channel(guild.id)
        if channel_id:
            channel = guild.get_channel_or_thread(channel_id)
    channel = channel or fallback
    if channel is None:
        return
    try:
        await channel.send(text, **kwargs)
    except discord.HTTPException:
        pass  # 沒權限或頻道被刪，公告失敗不影響遊戲
