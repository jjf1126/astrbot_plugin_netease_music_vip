import re
import aiohttp
import json
import base64
import html
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.api.provider import ProviderRequest

@register("astrbot_plugin_ncm_get", "AstrBot Developer", "解析网易云/QQ音乐链接，支持高兼容性搜索与上下文记录。", "2.5.4", "https://github.com/jjf1126/astrbot_plugin_ncm_get")
class MusicGetPlugin(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.config = config or {}
        self.auto_parse = self.config.get("auto_parse", True)
        self.ncm_cookie = self.config.get("cookie", "")
        self.qq_cookie = self.config.get("qq_cookie", "")
        self.inject_format = self.config.get("inject_format", "[系统附加信息] 用户分享了歌曲《{title}》，歌手：{artist}。以下是完整歌词：\n{lyrics}\n\n指令：请结合以上信息回复用户。请务必在你回复开头概括下歌曲主题。")

    # ==================== 搜索核心方法 (增强版) ====================
    async def _search_ncm_by_name(self, name: str) -> str:
        """根据歌名搜索网易云歌曲 ID"""
        # 使用 cloudsearch 接口，更稳定
        url = "http://music.163.com/api/cloudsearch/pc"
        params = {'s': name, 'type': 1, 'offset': 0, 'limit': 1}
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36',
            'Referer': 'http://music.163.com/'
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, headers=headers, timeout=5) as resp:
                    text = await resp.text()
                    if resp.status != 200:
                        logger.error(f"网易云 API 状态码异常: {resp.status}, 响应: {text[:100]}")
                        return ""
                    data = json.loads(text)
                    # 路径：result -> songs -> [0] -> id
                    if data.get('code') == 200 and data.get('result', {}).get('songs'):
                        return str(data['result']['songs'][0]['id'])
        except Exception as e:
            logger.error(f"网易云搜索执行异常: {e}")
        return ""

    async def _search_qq_by_name(self, name: str) -> str:
        """根据歌名搜索 QQ 音乐 songmid"""
        url = "https://c.y.qq.com/soso/fcgi-bin/client_search_cp"
        params = {'p': 1, 'n': 1, 'w': name, 'format': 'json', 'ct': 24, 'qqmusic_ver': 1298}
        headers = {
            'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 13_2_3 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/13.0.3 Mobile/15E148 Safari/604.1',
            'Referer': 'https://y.qq.com/'
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, headers=headers, timeout=5) as resp:
                    text = await resp.text()
                    # 处理可能存在的 JSONP 包装
                    if text.startswith('callback(') or text.startswith('jsonp'):
                        text = re.sub(r'^[a-zA-Z0-9_]+\((.*)\)$', r'\1', text.strip())
                    
                    data = json.loads(text)
                    # 路径：data -> song -> list -> [0] -> songmid
                    if data.get('code') == 0 and data.get('data', {}).get('song', {}).get('list'):
                        return data['data']['song']['list'][0]['songmid']
        except Exception as e:
            logger.error(f"QQ 音乐搜索执行异常: {e}")
        return ""

    # ==================== 网易云音乐核心方法 ====================
    async def _fetch_ncm_detail(self, song_id: str):
        url = f"http://music.163.com/api/song/detail/?id={song_id}&ids=[{song_id}]"
        headers = {'User-Agent': 'Mozilla/5.0', 'Referer': 'http://music.163.com/'}
        if self.ncm_cookie: headers['Cookie'] = self.ncm_cookie
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=5) as resp:
                    if resp.status == 200:
                        data = json.loads(await resp.text())
                        if data.get('songs'):
                            song = data['songs'][0]
                            return song.get('name', '未知歌曲'), "/".join([ar.get('name', '未知歌手') for ar in song.get('artists', [])])
        except Exception: pass
        return "未知歌曲", "未知歌手"

    async def _fetch_ncm_lyrics(self, song_id: str):
        url = f"http://music.163.com/api/song/lyric?id={song_id}&lv=1&kv=1&tv=-1"
        headers = {'User-Agent': 'Mozilla/5.0', 'Referer': 'http://music.163.com/'}
        if self.ncm_cookie: headers['Cookie'] = self.ncm_cookie
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=5) as resp:
                    if resp.status == 200:
                        data = json.loads(await resp.text())
                        if 'lrc' in data and 'lyric' in data['lrc']:
                            l = re.sub(r'\[\d{2}:\d{2}\.\d{2,3}\]', '', data['lrc']['lyric']).strip()
                            return re.sub(r'\n+', '\n', l) or "（纯音乐）"
        except Exception: pass
        return "未能获取到歌词"

    # ==================== QQ音乐核心方法 ====================
    async def _fetch_qq_detail(self, songmid: str):
        url = "https://c.y.qq.com/v8/fcg-bin/fcg_play_single_song.fcg"
        params = {'songmid': songmid, 'tmpl': 'v2.0', 'format': 'json'}
        headers = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://y.qq.com/'}
        if self.qq_cookie: headers['Cookie'] = self.qq_cookie
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, headers=headers, timeout=5) as resp:
                    if resp.status == 200:
                        data = json.loads(await resp.text())
                        if data.get('data'):
                            song = data['data'][0]
                            return song.get('title', '未知歌曲'), "/".join([ar.get('name', '未知歌手') for ar in song.get('singer', [])])
        except Exception: pass
        return "未知歌曲", "未知歌手"

    async def _fetch_qq_lyrics(self, songmid: str):
        url = "https://c.y.qq.com/lyric/fcgi-bin/fcg_query_lyric_new.fcg"
        params = {'songmid': songmid, 'format': 'json', 'nobase64': 0}
        headers = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://y.qq.com/'}
        if self.qq_cookie: headers['Cookie'] = self.qq_cookie
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, headers=headers, timeout=5) as resp:
                    if resp.status == 200:
                        data = json.loads(await resp.text())
                        if 'lyric' in data:
                            raw = base64.b64decode(data['lyric']).decode('utf-8')
                            clean = html.unescape(raw)
                            clean = re.sub(r'\[\d{2}:\d{2}\.\d{2}\]', '', clean).strip()
                            return re.sub(r'\\n|\n+', '\n', clean) or "（纯音乐）"
        except Exception: pass
        return "未能获取到歌词"

    # ==================== 辅助方法 ====================
    async def _resolve_qq_url(self, url: str) -> str:
        """穿透QQ音乐分享短链接获取真实ID"""
        smid = self._extract_qq_id_from_str(url)
        if smid: return smid
        try:
            headers = {'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 13_2_3 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/13.0.3 Mobile/15E148 Safari/604.1'}
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, allow_redirects=True, timeout=5) as resp:
                    real_url = str(resp.url)
                    smid = self._extract_qq_id_from_str(real_url)
                    if smid: return smid
                    html_text = await resp.text()
                    match = re.search(r'songmid["\']?\s*[:=]\s*["\']?([a-zA-Z0-9]{14})', html_text)
                    if match: return match.group(1)
        except Exception as e:
            logger.error(f"解析 QQ 短链接失败: {e}")
        return ""

    def _extract_qq_id_from_str(self, text: str) -> str:
        match = re.search(r'songDetail/([a-zA-Z0-9]+)', text) or \
                re.search(r'songmid=([a-zA-Z0-9]+)', text) or \
                re.search(r'msong/([a-zA-Z0-9]+)', text) or \
                re.search(r'/song/([a-zA-Z0-9]+)', text)
        return match.group(1) if match else ""

    def _extract_ncm_id(self, url: str) -> str:
        match = re.search(r'[?&]id=(\d+)', url) or re.search(r'/song/(\d+)', url)
        return match.group(1) if match else ""

    # ==================== 指令交互 ====================
    @filter.command("ncm_cookie")
    async def set_ncm_cookie(self, event: AstrMessageEvent, cookie: str = ""):
        '''设置网易云音乐 Cookie'''
        if not cookie:
            yield event.plain_result("请提供有效的网易云 Cookie 字符串。")
            return
        self.ncm_cookie = cookie
        yield event.plain_result(f"网易云 Cookie 已更新成功！长度：{len(cookie)}")

    @filter.command("qq_cookie")
    async def set_qq_cookie(self, event: AstrMessageEvent, cookie: str = ""):
        '''设置QQ音乐 Cookie'''
        if not cookie:
            yield event.plain_result("请提供有效的 QQ 音乐 Cookie 字符串。")
            return
        self.qq_cookie = cookie
        yield event.plain_result(f"QQ 音乐 Cookie 已更新成功！长度：{len(cookie)}")

    @filter.command("ncm_get")
    async def ncm_get(self, event: AstrMessageEvent, query: str):
        '''解析网易云链接或搜索歌曲名'''
        song_id = self._extract_ncm_id(query)
        if not song_id and query.isdigit():
            song_id = query
        if not song_id:
            song_id = await self._search_ncm_by_name(query)
            
        if not song_id:
            yield event.plain_result(f"未识别到有效链接且未搜索到歌曲：{query}")
            return
        title, artist = await self._fetch_ncm_detail(song_id)
        lyrics = await self._fetch_ncm_lyrics(song_id)
        yield event.plain_result(f"网易云解析成功！\n《{title}》- {artist}\nID: {song_id}\n\n【歌词】\n{lyrics[:150]}...")

    @filter.command("qq_get")
    async def qq_get(self, event: AstrMessageEvent, query: str):
        '''解析QQ音乐链接或搜索歌曲名'''
        songmid = await self._resolve_qq_url(query)
        if not songmid:
            songmid = await self._search_qq_by_name(query)
            
        if not songmid:
            yield event.plain_result(f"未识别到有效链接且未搜索到歌曲：{query}")
            return
        title, artist = await self._fetch_qq_detail(songmid)
        lyrics = await self._fetch_qq_lyrics(songmid)
        yield event.plain_result(f"QQ音乐解析成功！\n《{title}》- {artist}\nSongmid: {songmid}\n\n【歌词】\n{lyrics[:150]}...")

    # ==================== 自动拦截器 (含上下文持久化) ====================
    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest):
        if not self.auto_parse: return
        try:
            user_text = event.message_str
            
            # QQ 音乐解析
            qq_urls = re.findall(r'(https?://[a-zA-Z0-9\.\-]*y\.qq\.com[^\s]+)', user_text)
            if qq_urls:
                smid = await self._resolve_qq_url(qq_urls[0])
                if smid:
                    t, a = await self._fetch_qq_detail(smid)
                    l = await self._fetch_qq_lyrics(smid)
                    inject_text = self.inject_format.format(title=t, artist=a, lyrics=l, song_id=smid, iframe='')
                    req.system_prompt += f"\n\n{inject_text}"
                    req.request_messages.append({"role": "system", "content": inject_text})
                    logger.info(f"成功注入记录: QQ音乐《{t}》")

            # 网易云解析
            ncm_urls = re.findall(r'(https?://[a-zA-Z0-9\.\-]*163\.com[^\s]+)', user_text)
            if ncm_urls:
                sid = self._extract_ncm_id(ncm_urls[0])
                if sid:
                    t, a = await self._fetch_ncm_detail(sid)
                    l = await self._fetch_ncm_lyrics(sid)
                    inject_text = self.inject_format.format(title=t, artist=a, lyrics=l, song_id=sid, iframe='')
                    req.system_prompt += f"\n\n{inject_text}"
                    req.request_messages.append({"role": "system", "content": inject_text})
                    logger.info(f"成功注入记录: 网易云《{t}》")
        except Exception as e:
            logger.error(f"注入音乐异常: {e}")

    # ==================== LLM 函数工具 ====================
    @filter.llm_tool(name="get_qq_song_info")
    async def get_qq_song_info(self, event: AstrMessageEvent, identifier: str):
        """
        获取QQ音乐歌曲的详细信息和歌词。支持 URL、songmid，不支持歌曲名称。
        Args:
            identifier (string): 链接、songmid。
        """
        try:
            urls = re.findall(r'(https?://[a-zA-Z0-9\.\-]*y\.qq\.com[^\s]+)', identifier)
            if urls:
                clean_id = await self._resolve_qq_url(urls[0])
            elif re.match(r'^[a-zA-Z0-9]{14}$', identifier):
                clean_id = identifier
            else:
                # 移除了通过歌曲名称搜索的逻辑，直接返回错误提示
                return {"error": "不支持使用歌曲名称搜索，请提供有效的 QQ 音乐链接或 songmid。"}
                
            if not clean_id: return {"error": "未解析到有效的 songmid，请检查链接是否正确。"}
            title, artist = await self._fetch_qq_detail(clean_id)
            lyrics = await self._fetch_qq_lyrics(clean_id)
            return {"status": "success", "title": title, "artist": artist, "lyrics": lyrics}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    @filter.llm_tool(name="get_ncm_song_info")
    async def get_ncm_song_info(self, event: AstrMessageEvent, identifier: str):
        """
        获取网易云歌曲的详细信息和歌词。支持 URL、数字 ID 或歌曲名称。
        Args:
            identifier (string): 链接、数字 ID 或歌曲名。
        """
        try:
            urls = re.findall(r'(https?://[a-zA-Z0-9\.\-]*163\.com[^\s]+)', identifier)
            if urls:
                clean_id = self._extract_ncm_id(urls[0])
            elif str(identifier).isdigit():
                clean_id = str(identifier)
            else:
                clean_id = await self._search_ncm_by_name(identifier)
                
            if not clean_id: return {"error": "未找到匹配的歌曲，请尝试提供更精准的歌名或歌手。"}
            title, artist = await self._fetch_ncm_detail(clean_id)
            lyrics = await self._fetch_ncm_lyrics(clean_id)
            return {"status": "success", "title": title, "artist": artist, "lyrics": lyrics}
        except Exception as e:
            return {"status": "error", "message": str(e)}
