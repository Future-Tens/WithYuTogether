import aiohttp
import os
import json
import asyncio

class ValorantAPI:
    BASE_URL = "https://api.henrikdev.xyz/valorant" # 或者是指定的 HenrikDev API 節點

    @classmethod
    def get_headers(cls):
        try:
            if os.path.exists("config.json"):
                with open("config.json", "r", encoding="utf-8") as f:
                    return {"Authorization": json.load(f).get("valorant_api_key", ""), "Accept": "application/json"}
        except Exception:
            pass
        return {"Accept": "application/json"}

    @classmethod
    async def fetch_puuid(cls, riot_id: str, tagline: str):
        """驗證並抓取帳號 PUUID"""
        url = f"{cls.BASE_URL}/v2/account/{riot_id}/{tagline}"
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(url, headers=cls.get_headers(), timeout=10) as resp:
                    if resp.status == 200:
                        data = (await resp.json()).get("data", {})
                        return {
                            "puuid": data.get("puuid"),
                            "region": data.get("region", "ap"),
                            "riot_id": data.get("name"),
                            "tagline": data.get("tag")
                        }
                    return None
            except Exception:
                return None

    @classmethod
    async def get_mmr_details(cls, puuid: str, region: str = "ap"):
        """獲取積分賽季數據"""
        url = f"{cls.BASE_URL}/v2/by-puuid/mmr/{region}/{puuid}"
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(url, headers=cls.get_headers(), timeout=10) as resp:
                    return await resp.json() if resp.status == 200 else None
            except Exception:
                return None

    @classmethod
    async def get_match_history(cls, puuid: str, region: str = "ap", limit: int = 3, mode_filter: str = "all"):
        """
        抓取近期對局戰績 (支援模式篩選)
        """
        # 💡 核心優化：如果是 all，就不帶 filter 參數；如果是特定模式，則加上 filter 查詢
        filter_query = f"&filter={mode_filter}" if mode_filter != "all" else ""
        url = f"{cls.BASE_URL}/v3/by-puuid/matches/{region}/{puuid}?size={limit}{filter_query}"
        
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(url, headers=cls.get_headers(), timeout=10) as response:
                    if response.status == 200:
                        return await response.json()
                    return None
            except Exception:
                return None