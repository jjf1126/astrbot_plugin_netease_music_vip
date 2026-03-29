import os
import json
import asyncio
from typing import Dict, Any

from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register, StarTools
from astrbot.api import logger, AstrBotConfig
import astrbot.api.message_components as Comp

# 尝试导入 pyncm 库
try:
    import pyncm
    from pyncm.apis import cloudsearch, track, login
    PYNCM_AVAILABLE = True
except ImportError:
    PYNCM_AVAILABLE = False
    logger.error("未安装 pyncm 库，网易云音乐插件可能无法正常工作。请执行 pip install pyncm")

@register("astrbot_plugin_netease_music", "Developer", "网易云音乐插件。支持获取歌曲信息、评论，发送音乐卡片，并提供大模型函数调用。", "1.0.0", "https://github.com/developer/astrbot_plugin_netease_music")
class NeteaseMusicPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.data_dir = StarTools.get_data_dir()
        self.user_data_file = os.path.join(str(self.data_dir), "users.json")
        
        # 确保数据目录存在
        if not os.path.exists(self.data_dir):
            os.makedirs(self.data_dir, exist_ok=True)
            
        # 初始化用户数据
        self.user_data = self._load_user_data()
        
        # 初始化全局配置
        self._init_global_cookie()

    def _load_user_data(self) -> Dict[str, Any]:
        """从本地加载用户凭据数据（仅限合法请求使用）"""
        if os.path.exists(self.user_data_file):
            try:
                with open(self.user_data_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"加载用户数据失败: {e}")
        return {}

    def _save_user_data(self):
        """保存用户凭据数据到本地"""
        try:
            with open(self.user_data_file, "w", encoding="utf-8") as f:
                json.dump(self.user_data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存用户数据失败: {e}")

    def _init_global_cookie(self):
        """初始化全局 Cookie"""
        if not PYNCM_AVAILABLE:
            return
            
        global_cookie = self.config.get("global_cookie", "")
        if global_cookie:
            try:
                # 简单解析 Cookie 字符串并设置
                # 注意：实际生产环境中可能需要更严谨的 cookie 解析
                login.LoginViaCookie(global_cookie)
                logger.info("已加载全局网易云音乐 Cookie")
            except Exception as e:
                logger.error(f"加载全局 Cookie 失败: {e}")

    async def _search_song(self, keyword: str) -> dict:
        """异步搜索歌曲"""
        if not PYNCM_AVAILABLE:
            raise RuntimeError("依赖库 pyncm 未安装")
            
        limit = self.config.get("search_limit", 3)
        
        def _do_search():
            res = cloudsearch.GetSearchResult(keyword, stype=1, limit=limit)
            if res and "result" in res and "songs" in res["result"] and len(res["result"]["songs"]) > 0:
                return res["result"]["songs"][0]
            return None
            
        return await asyncio.to_thread(_do_search)

    @filter.command("ncm_login")
    async def ncm_login(self, event: AstrMessageEvent, cookie_str: str):
        """设置并验证网易云音乐账号登录状态"""
        allow_user_login = self.config.get("allow_user_login", False)
        
        # 权限检查
        if not allow_user_login and not event.get_sender_id() in self.context.get_config().get("admin", []):
            yield event.plain_result("抱歉，当前仅允许管理员设置登录凭证。")
            return

        try:
            # 在独立线程中尝试登录
            def _do_login():
                return login.LoginViaCookie(cookie_str)
                
            res = await asyncio.to_thread(_do_login)
            if res and res.get("code") == 200:
                user_id = event.get_sender_id()
                self.user_data[user_id] = cookie_str
                self._save_user_data()
                yield event.plain_result("✅ 网易云音乐账号登录状态设置成功！")
            else:
                yield event.plain_result("❌ 登录失败，请检查 Cookie 凭证是否有效。")
        except Exception as e:
            logger.error(f"网易云登录异常: {e}")
            yield event.plain_result(f"登录发生异常，请联系管理员。")

    @filter.command("ncm_info")
    async def ncm_info(self, event: AstrMessageEvent, keyword: str):
        """查询指定网易云歌曲信息（包含歌词与热门评论）"""
        try:
            song = await self._search_song(keyword)
            if not song:
                yield event.plain_result(f"未找到与“{keyword}”相关的歌曲。")
                return
                
            song_id = song["id"]
            song_name = song["name"]
            artist_name = "/".join([ar["name"] for ar in song.get("ar", [])])
            
            # 获取歌词和评论
            def _get_details():
                lyrics_res = track.GetTrackLyrics(song_id)
                comments_res = track.GetTrackComments(song_id, limit=self.config.get("max_comments", 5))
                return lyrics_res, comments_res
                
            lyrics_res, comments_res = await asyncio.to_thread(_get_details)
            
            # 解析歌词
            lyric_text = "暂无歌词"
            if "lrc" in lyrics_res and "lyric" in lyrics_res["lrc"]:
                # 简单截取前 200 个字符以防消息过长
                lyric_text = lyrics_res["lrc"]["lyric"][:200] + "..."
                
            # 解析评论
            comments_text = ""
            if "hotComments" in comments_res:
                hot_comments = comments_res["hotComments"]
                for i, c in enumerate(hot_comments):
                    comments_text += f"{i+1}. {c['user']['nickname']}: {c['content']}\n"
                    
            if not comments_text:
                comments_text = "暂无热门评论"
                
            reply_msg = (
                f"🎵 歌曲：{song_name} - {artist_name}\n"
                f"🆔 ID：{song_id}\n"
                f"📝 歌词片段：\n{lyric_text}\n\n"
                f"💬 热门评论：\n{comments_text}"
            )
            yield event.plain_result(reply_msg)
            
        except Exception as e:
            logger.error(f"获取歌曲信息失败: {e}")
            yield event.plain_result(f"获取歌曲信息时发生错误: {e}")

    @filter.command("ncm_card")
    async def ncm_card(self, event: AstrMessageEvent, keyword: str):
        """在QQ聊天中发送指定歌曲的音乐卡片"""
        try:
            song = await self._search_song(keyword)
            if not song:
                yield event.plain_result(f"未找到与“{keyword}”相关的歌曲。")
                return
                
            song_id = str(song["id"])
            
            # 尝试发送音乐卡片 (使用 Music 消息段)
            # 注意：平台适配器需支持 Music 类型。如不支持，则根据配置回退为文本
            fallback = self.config.get("fallback_to_text", True)
            
            platform_name = event.get_platform_name()
            if platform_name in ["aiocqhttp"]:
                # QQ 平台发送音乐卡片
                chain = [Comp.Music(type="163", id=song_id)]
                yield event.chain_result(chain)
            else:
                if fallback:
                    song_name = song["name"]
                    artist_name = "/".join([ar["name"] for ar in song.get("ar", [])])
                    url = f"https://music.163.com/#/song?id={song_id}"
                    yield event.plain_result(f"🎵 {song_name} - {artist_name}\n🔗 链接: {url}")
                else:
                    yield event.plain_result("当前平台不支持发送音乐卡片消息。")
                    
        except Exception as e:
            logger.error(f"发送音乐卡片失败: {e}")
            yield event.plain_result("发送音乐卡片时发生错误。")

    @filter.llm_tool(name="get_netease_music_info")
    async def get_netease_music_info(self, event: AstrMessageEvent, keyword: str) -> MessageEventResult:
        """
        获取网易云音乐指定歌曲的详细信息，包含歌曲ID、歌手、部分歌词及热门评论。
        当用户询问某首歌的歌词、评论或信息时调用此工具。

        Args:
            keyword(string): 歌曲名称或关键词
        """
        if not self.config.get("enable_function_calling", True):
            yield event.plain_result("大模型函数调用功能已在配置中关闭。")
            return
            
        try:
            song = await self._search_song(keyword)
            if not song:
                yield event.plain_result(f"未找到相关歌曲: {keyword}")
                return
                
            song_id = song["id"]
            
            def _get_details():
                return track.GetTrackLyrics(song_id), track.GetTrackComments(song_id, limit=3)
                
            lyrics_res, comments_res = await asyncio.to_thread(_get_details)
            
            lyric_text = "无"
            if "lrc" in lyrics_res and "lyric" in lyrics_res["lrc"]:
                lyric_text = lyrics_res["lrc"]["lyric"][:300] # 提供给大模型的上下文不宜过长
                
            comments_text = []
            if "hotComments" in comments_res:
                for c in comments_res["hotComments"]:
                    comments_text.append(f"{c['user']['nickname']}: {c['content']}")
                    
            result_data = {
                "song_name": song["name"],
                "artists": [ar["name"] for ar in song.get("ar", [])],
                "song_id": song_id,
                "lyrics_snippet": lyric_text,
                "hot_comments": comments_text
            }
            
            # 返回 JSON 格式字符串供大模型解析
            yield event.plain_result(json.dumps(result_data, ensure_ascii=False))
            
        except Exception as e:
            logger.error(f"Tool调用失败: {e}")
            yield event.plain_result(f"获取网易云音乐数据失败: {str(e)}")