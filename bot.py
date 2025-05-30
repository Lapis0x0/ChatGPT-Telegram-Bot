import os
import re
import sys
sys.dont_write_bytecode = True
import logging
import traceback
import utils.decorators as decorators
import utils.proactive_messaging as proactive_messaging
from utils.memory_integration import process_memory, get_memory_enhanced_prompt, add_explicit_memory, list_memories, forget_memory, track_conversation, force_summarize_memory
import utils.proactive_messaging as proactive_messaging
from utils.message_splitter import process_structured_messages, get_structured_message_prompt
from utils.memory_commands import list_new_memories, add_new_memory, delete_new_memory

from md2tgmd.src.md2tgmd import escape, split_code, replace_all
from aient.src.aient.utils.prompt import translator_prompt
from aient.src.aient.utils.scripts import Document_extract, claude_replace
from aient.src.aient.core.utils import get_engine, get_image_message, get_text_message
import config
from config import (
    WEB_HOOK,
    PORT,
    BOT_TOKEN,
    GET_MODELS,
    GOOGLE_AI_API_KEY,
    VERTEX_PROJECT_ID,
    VERTEX_PRIVATE_KEY,
    VERTEX_CLIENT_EMAIL,
    Users,
    PREFERENCES,
    LANGUAGES,
    PLUGINS,
    RESET_TIME,
    get_robot,
    reset_ENGINE,
    get_current_lang,
    update_info_message,
    update_menu_buttons,
    remove_no_text_model,
    update_initial_model,
    update_models_buttons,
    update_language_status,
    update_first_buttons_message,
    get_all_available_models,
    get_model_groups,
    CUSTOM_MODELS_LIST,
    MODEL_GROUPS,
)

from utils.i18n import strings
from utils.scripts import GetMesageInfo, safe_get, is_emoji

from telegram.constants import ChatAction
from telegram import BotCommand, InlineKeyboardMarkup, InlineQueryResultArticle, InputTextMessageContent, Update, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, InputMediaPhoto, InlineKeyboardButton
from telegram.ext import CommandHandler, MessageHandler, ApplicationBuilder, filters, CallbackQueryHandler, Application, AIORateLimiter, InlineQueryHandler, ContextTypes
from datetime import timedelta, datetime
import pytz

# 定义东八区时区
CHINA_TZ = pytz.timezone('Asia/Shanghai')

import asyncio
lock = asyncio.Lock()
event = asyncio.Event()
stop_event = asyncio.Event()
time_out = 600

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger()

logging.getLogger("httpx").setLevel(logging.CRITICAL)
logging.getLogger("chromadb.telemetry.posthog").setLevel(logging.WARNING)
logging.getLogger('googleapicliet.discovery_cache').setLevel(logging.ERROR)

class SpecificStringFilter(logging.Filter):
    def __init__(self, specific_string):
        super().__init__()
        self.specific_string = specific_string

    def filter(self, record):
        return self.specific_string not in record.getMessage()

specific_string = "httpx.RemoteProtocolError: Server disconnected without sending a response."
my_filter = SpecificStringFilter(specific_string)

update_logger = logging.getLogger("telegram.ext.Updater")
update_logger.addFilter(my_filter)
update_logger = logging.getLogger("root")
update_logger.addFilter(my_filter)

# 定义一个缓存来存储消息
from collections import defaultdict
message_cache = defaultdict(lambda: [])
time_stamps = defaultdict(lambda: [])

@decorators.PrintMessage
@decorators.GroupAuthorization
@decorators.Authorization
@decorators.APICheck
async def command_bot(update, context, language=None, prompt=translator_prompt, title="", has_command=True):
    stop_event.clear()
    message, rawtext, image_url, chatid, messageid, reply_to_message_text, update_message, message_thread_id, convo_id, file_url, reply_to_message_file_content, voice_text = await GetMesageInfo(update, context)

    # 异步处理记忆，但不阻塞主对话流程
    if not has_command and config.ChatGPTbot is not None and message is not None:
        # 跟踪对话，它会在达到一定轮数后自动总结
        asyncio.create_task(track_conversation(str(convo_id), "user", message, config.ChatGPTbot))
        
        # 分析用户消息并调整主动对话欲望
        if proactive_messaging.PROACTIVE_AGENT_ENABLED:
            # asyncio.create_task(proactive_messaging.analyze_message_for_desire(chatid, message))  # 已废弃，无需再调用
            pass
        
        # asyncio.create_task(proactive_messaging.analyze_message_for_desire(chatid, message))  # 已废弃，无需再调用
    if has_command == False or len(context.args) > 0:
        if has_command:
            message = ' '.join(context.args)
        pass_history = Users.get_config(convo_id, "PASS_HISTORY")
        if prompt and has_command:
            if translator_prompt == prompt:
                if language == "english":
                    prompt = prompt.format(language)
                else:
                    prompt = translator_prompt
                pass_history = 0
            message = prompt + message
        if message == None:
            message = voice_text
        # print("message", message)
        if message and len(message) == 1 and is_emoji(message):
            return

        message_has_nick = False
        botNick = config.NICK.lower() if config.NICK else None
        if rawtext and rawtext.split()[0].lower() == botNick:
            message_has_nick = True

        if message_has_nick and update_message.reply_to_message and update_message.reply_to_message.caption and not message:
            message = update_message.reply_to_message.caption

        if message:
            if pass_history >= 3:
                # 移除已存在的任务（如果有）
                remove_job_if_exists(convo_id, context)
                # 添加新的定时任务
                context.job_queue.run_once(
                    scheduled_function,
                    when=timedelta(seconds=RESET_TIME),
                    chat_id=chatid,
                    name=convo_id
                )

            bot_info_username = None
            try:
                bot_info = await context.bot.get_me(read_timeout=time_out, write_timeout=time_out, connect_timeout=time_out, pool_timeout=time_out)
                bot_info_username = bot_info.username
            except Exception as e:
                bot_info_username = update_message.reply_to_message.from_user.username
                print("error:", e)

            if update_message.reply_to_message \
            and update_message.from_user.is_bot == False \
            and (update_message.reply_to_message.from_user.username == bot_info_username or message_has_nick):
                if update_message.reply_to_message.from_user.is_bot and Users.get_config(convo_id, "TITLE") == True:
                    message = message + "\n" + '\n'.join(reply_to_message_text.split('\n')[1:])
                else:
                    if reply_to_message_text:
                        message = message + "\n" + reply_to_message_text
                    if reply_to_message_file_content:
                        message = message + "\n" + reply_to_message_file_content
            elif update_message.reply_to_message and update_message.reply_to_message.from_user.is_bot \
            and update_message.reply_to_message.from_user.username != bot_info_username:
                return

            robot, role, api_key, api_url = get_robot(convo_id)
            engine = Users.get_config(convo_id, "engine")

            if Users.get_config(convo_id, "LONG_TEXT"):
                async with lock:
                    message_cache[convo_id].append(message)
                    import time
                    time_stamps[convo_id].append(time.time())
                    if len(message_cache[convo_id]) == 1:
                        print("first message len:", len(message_cache[convo_id][0]))
                        if len(message_cache[convo_id][0]) > 800:
                            event.clear()
                        else:
                            event.set()
                    else:
                        return
                try:
                    await asyncio.wait_for(event.wait(), timeout=2)
                except asyncio.TimeoutError:
                    print("asyncio.wait timeout!")

                intervals = [
                    time_stamps[convo_id][i] - time_stamps[convo_id][i - 1]
                    for i in range(1, len(time_stamps[convo_id]))
                ]
                if intervals:
                    print(f"Chat ID {convo_id} 时间间隔: {intervals}，总时间：{sum(intervals)}")

                message = "\n".join(message_cache[convo_id])
                message_cache[convo_id] = []
                time_stamps[convo_id] = []
            # if Users.get_config(convo_id, "TYPING"):
            #     await context.bot.send_chat_action(chat_id=chatid, message_thread_id=message_thread_id, action=ChatAction.TYPING)
            if Users.get_config(convo_id, "TITLE"):
                title = f"`🤖️ {engine}`\n\n"
            if Users.get_config(convo_id, "REPLY") == False:
                messageid = None

            engine_type, _ = get_engine({"base_url": api_url}, endpoint=None, original_model=engine)
            if robot.__class__.__name__ == "chatgpt":
                engine_type = "gpt"
            if image_url:
                message_list = []
                image_message = await get_image_message(image_url, engine_type)
                text_message = await get_text_message(message, engine_type)
                message_list.append(text_message)
                message_list.append(image_message)
                message = message_list
            elif file_url:
                image_url = file_url
                message = await Document_extract(file_url, image_url, engine_type) + message

            await getChatGPT(update_message, context, title, robot, message, chatid, messageid, convo_id, message_thread_id, pass_history, api_key, api_url, engine)
    else:
        message = await context.bot.send_message(
            chat_id=chatid,
            message_thread_id=message_thread_id,
            text=escape(strings['message_command_text_none'][get_current_lang(convo_id)]),
            parse_mode='MarkdownV2',
            reply_to_message_id=messageid,
        )

async def delete_message(update, context, messageid = [], delay=60):
    await asyncio.sleep(delay)
    if isinstance(messageid, list):
        for mid in messageid:
            try:
                await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=mid)
            except Exception as e:
                pass
                # print('\033[31m')
                # print("delete_message error", e)
                # print('\033[0m')

from telegram.error import Forbidden, TelegramError
async def is_bot_blocked(bot, user_id: int) -> bool:
    try:
        # 尝试向用户发送一条测试消息
        await bot.send_chat_action(chat_id=user_id, action="typing")
        return False  # 如果成功发送，说明机器人未被封禁
    except Forbidden:
        print("error:", user_id, "已封禁机器人")
        return True  # 如果收到Forbidden错误，说明机器人被封禁
    except TelegramError:
        # 处理其他可能的错误
        return False  # 如果是其他错误，我们假设机器人未被封禁

async def getChatGPT(update_message, context, title, robot, message, chatid, messageid, convo_id, message_thread_id, pass_history=0, api_key=None, api_url=None, engine = None):
    lastresult = title
    text = message
    result = ""
    tmpresult = ""
    modifytime = 0
    time_out = 600
    image_has_send = 0
    model_name = engine
    language = Users.get_config(convo_id, "language")
    if "claude" in model_name:
        system_prompt = Users.get_config(convo_id, "claude_systemprompt")
    else:
        system_prompt = Users.get_config(convo_id, "systemprompt")
    
    # 添加当前东八区日期和时间到系统提示词
    current_datetime = datetime.now(CHINA_TZ)
    current_date = current_datetime.strftime("%Y-%m-%d")
    current_time = current_datetime.strftime("%H:%M")
    system_prompt = f"当前日期和时间（东八区）：{current_date} {current_time}\n\n{system_prompt}"
    
    # 添加结构化消息的提示词
    structured_message_prompt = get_structured_message_prompt()
    system_prompt = f"{system_prompt}\n\n{structured_message_prompt}"
        
    # 使用增强了记忆的系统提示词
    memory_enhanced_prompt = get_memory_enhanced_prompt(str(convo_id), system_prompt)
    system_prompt = memory_enhanced_prompt

    plugins = Users.extract_plugins_config(convo_id)

    Frequency_Modification = 20
    if "gpt-4o" in model_name:
        Frequency_Modification = 25
    if message_thread_id or convo_id.startswith("-"):
        Frequency_Modification = 35
    if "gemini" in model_name and (GOOGLE_AI_API_KEY or (VERTEX_CLIENT_EMAIL and VERTEX_PRIVATE_KEY and VERTEX_PROJECT_ID)):
        Frequency_Modification = 1

    # 添加当前时间戳到用户消息
    current_datetime = datetime.now(CHINA_TZ)
    message_timestamp = current_datetime.timestamp()
    formatted_time = current_datetime.strftime("%Y-%m-%d %H:%M")
    
    # 将用户消息添加到对话历史时包含时间戳，并在内容中显示时间
    if not pass_history:
        # 在消息内容前添加时间信息
        text_with_time = f"[{formatted_time}] {text}"
        
        robot.add_to_conversation({
            "role": "user", 
            "content": text_with_time,
            "timestamp": str(message_timestamp),
            "formatted_time": formatted_time
        }, convo_id)

    if not await is_bot_blocked(context.bot, chatid):
        answer_messageid = (await context.bot.send_message(
            chat_id=chatid,
            message_thread_id=message_thread_id,
            text=escape(strings['message_think'][get_current_lang(convo_id)]),
            parse_mode='MarkdownV2',
            reply_to_message_id=messageid,
        )).message_id
    else:
        return

    try:
        # 用于检测是否可能是JSON格式的消息
        might_be_json = False
        json_detection_done = False
        first_chunk = True
        
        # print("text", text)
        # 传递用户ID给Gemini模型，启用基于function calling的记忆系统
        user_id = str(update_message.from_user.id) if update_message and update_message.from_user else None
        async for data in robot.ask_stream_async(text, convo_id=convo_id, model=model_name, language=language, system_prompt=system_prompt, pass_history=pass_history, api_key=api_key, api_url=api_url, user_id=user_id, plugins=plugins):
        # for data in robot.ask_stream(text, convo_id=convo_id, pass_history=pass_history, model=model_name):
            if stop_event.is_set() and convo_id == target_convo_id and answer_messageid < reset_mess_id:
                return
            if "message_search_stage_" not in data:
                result = result + data
            
            # 在前几个数据块中检测是否可能是JSON格式
            if first_chunk and not json_detection_done:
                # 如果是第一个数据块，检查是否可能是JSON开头
                first_chunk = False
                result_so_far = result.strip()
                
                # 检查是否可能是JSON格式
                if (result_so_far.startswith("{") or 
                    result_so_far.lower().startswith("json") or 
                    result_so_far.startswith("```")):
                    logging.info(f"检测到可能是JSON格式的消息开头: {result_so_far[:20]}...")
                    might_be_json = True
                
                json_detection_done = True
            
            # 如果可能是JSON格式，我们不进行流式更新，而是等待完整消息
            if might_be_json:
                # 只在接收完所有数据后更新一次
                continue
                
            tmpresult = result
            if re.sub(r"```", '', result.split("\n")[-1]).count("`") % 2 != 0:
                tmpresult = result + "`"
            if sum([line.strip().startswith("```") for line in result.split('\n')]) % 2 != 0:
                tmpresult = tmpresult + "\n```"
            tmpresult = title + tmpresult
            if "claude" in model_name:
                tmpresult = claude_replace(tmpresult)
            if "message_search_stage_" in data:
                tmpresult = strings[data][get_current_lang(convo_id)]
            history = robot.conversation[convo_id]
            if safe_get(history, -2, "tool_calls", 0, 'function', 'name') == "generate_image" and not image_has_send and safe_get(history, -1, 'content'):
                image_result = history[-1]['content'].split('\n\n')[1]
                await context.bot.send_photo(chat_id=chatid, photo=image_result, reply_to_message_id=messageid)
                image_has_send = 1
            modifytime = modifytime + 1

            split_len = 3500
            if len(tmpresult) > split_len and Users.get_config(convo_id, "LONG_TEXT_SPLIT"):
                Frequency_Modification = 40

                # print("tmpresult", tmpresult)
                replace_text = replace_all(tmpresult, r"(```[\D\d\s]+?```)", split_code)
                if "@|@|@|@" in replace_text:
                    print("@|@|@|@", replace_text)
                    split_messages = replace_text.split("@|@|@|@")
                    send_split_message = split_messages[0]
                    result = split_messages[1][:-4]
                else:
                    print("replace_text", replace_text)
                    if replace_text.strip().endswith("```"):
                        replace_text = replace_text.strip()[:-4]
                    split_messages_new = []
                    split_messages = replace_text.split("```")
                    for index, item in enumerate(split_messages):
                        if index % 2 == 1:
                            item = "```" + item
                            if index != len(split_messages) - 1:
                                item = item + "```"
                            split_messages_new.append(item)
                        if index % 2 == 0:
                            item_split_new = []
                            item_split = item.split("\n\n")
                            for sub_index, sub_item in enumerate(item_split):
                                if sub_index % 2 == 1:
                                    sub_item = "\n\n" + sub_item
                                    if sub_index != len(item_split) - 1:
                                        sub_item = sub_item + "\n\n"
                                    item_split_new.append(sub_item)
                                if sub_index % 2 == 0:
                                    item_split_new.append(sub_item)
                            split_messages_new.extend(item_split_new)

                    split_index = 0
                    for index, _ in enumerate(split_messages_new):
                        if len("".join(split_messages_new[:index])) < split_len:
                            split_index += 1
                            continue
                        else:
                            break
                    # print("split_messages_new", split_messages_new)
                    send_split_message = ''.join(split_messages_new[:split_index])
                    matches = re.findall(r"(```.*?\n)", send_split_message)
                    if len(matches) % 2 != 0:
                        send_split_message = send_split_message + "```\n"
                    # print("send_split_message", send_split_message)
                    tmp = ''.join(split_messages_new[split_index:])
                    if tmp.strip().endswith("```"):
                        result = tmp[:-4]
                    else:
                        result = tmp
                    # print("result", result)
                    matches = re.findall(r"(```.*?\n)", send_split_message)
                    result_matches = re.findall(r"(```.*?\n)", result)
                    # print("matches", matches)
                    # print("result_matches", result_matches)
                    if len(result_matches) > 0 and result_matches[0].startswith("```\n") and len(result_matches) >= 2:
                        result = matches[-2] + result
                    # print("result", result)

                title = ""
                if lastresult != escape(send_split_message, italic=False):
                    try:
                        await context.bot.edit_message_text(
                            chat_id=chatid,
                            message_id=answer_messageid,
                            text=escape(send_split_message, italic=False),
                            parse_mode='MarkdownV2',
                            disable_web_page_preview=True,
                            read_timeout=time_out,
                            write_timeout=time_out,
                            pool_timeout=time_out,
                            connect_timeout=time_out
                        )
                        lastresult = escape(send_split_message, italic=False)
                    except Exception as e:
                        if "parse entities" in str(e):
                            await context.bot.edit_message_text(chat_id=chatid, message_id=answer_messageid, text=send_split_message, disable_web_page_preview=True, read_timeout=time_out, write_timeout=time_out, pool_timeout=time_out, connect_timeout=time_out)

            # 如果不是可能的JSON格式，则进行正常的流式更新
            if not might_be_json:
                now_result = escape(tmpresult, italic=False)
                if now_result and (modifytime % Frequency_Modification == 0 and lastresult != now_result) or "message_search_stage_" in data:
                    try:
                        await context.bot.edit_message_text(chat_id=chatid, message_id=answer_messageid, text=now_result, parse_mode='MarkdownV2', disable_web_page_preview=True, read_timeout=time_out, write_timeout=time_out, pool_timeout=time_out, connect_timeout=time_out)
                        lastresult = now_result
                    except Exception as e:
                        # print('\033[31m')
                        print("1: Unexpected error4:", type(e), str(e))
                        # print('\033[0m')
            
            modifytime += 1
            
        # 当完成对话生成后，跟踪机器人的回复
        try:
            # 添加当前时间戳到机器人回复
            current_datetime = datetime.now(CHINA_TZ)
            response_timestamp = current_datetime.timestamp()
            formatted_time = current_datetime.strftime("%Y-%m-%d %H:%M")
            
            # 将机器人回复添加到对话历史时包含时间戳，并在内容中显示时间
            if not pass_history:
                # 在回复内容前添加时间信息
                result_with_time = f"[{formatted_time}] {result}"
                
                robot.add_to_conversation({
                    "role": "assistant", 
                    "content": result_with_time,
                    "timestamp": str(response_timestamp),
                    "formatted_time": formatted_time
                }, convo_id)
            
            asyncio.create_task(track_conversation(str(convo_id), "assistant", result, robot))
        except Exception as e:
            # 即使记忆跟踪失败，也不影响主对话
            logging.error(f"跟踪机器人回复时出错: {str(e)}")
        
        tmpresult = result.replace("```", "")

    except Exception as e:
        print('\033[31m')
        traceback.print_exc()
        print(tmpresult)
        print('\033[0m')
        api_key = Users.get_config(convo_id, "api_key")
        systemprompt = Users.get_config(convo_id, "systemprompt")
        if api_key:
            robot.reset(convo_id=convo_id, system_prompt=systemprompt)
        if "parse entities" in str(e):
            await context.bot.edit_message_text(chat_id=chatid, message_id=answer_messageid, text=tmpresult, disable_web_page_preview=True, read_timeout=time_out, write_timeout=time_out, pool_timeout=time_out, connect_timeout=time_out)
        else:
            tmpresult = f"{tmpresult}\n\n`{e}`"
    print(tmpresult)

    # 添加图片URL检测和发送
    if image_has_send == 0:
        image_extensions = r'(https?://[^\s<>\"()]+(?:\.(?:webp|jpg|jpeg|png|gif)|/image)[^\s<>\"()]*)'
        image_urls = re.findall(image_extensions, tmpresult, re.IGNORECASE)
        image_urls_result = [url[0] if isinstance(url, tuple) else url for url in image_urls]
        if image_urls_result:
            try:
                # Limit the number of images to 10 (Telegram limit for albums)
                image_urls_result = image_urls_result[:10]

                # We send an album with all images
                media_group = []
                for img_url in image_urls_result:
                    media_group.append(InputMediaPhoto(media=img_url))

                await context.bot.send_media_group(
                    chat_id=chatid,
                    media=media_group,
                    message_thread_id=message_thread_id,
                    reply_to_message_id=messageid,
                )
            except Exception as e:
                logger.warning(f"Failed to send image(s): {str(e)}")

    # 如果可能是JSON格式，我们在接收完所有数据后再处理
    if might_be_json:
        logging.info(f"模型输出完成，检测到可能是JSON格式的消息，尝试处理结构化消息")
        try:
            # 处理结构化消息，检查是否需要拆分发送
            processed_result = await process_structured_messages(
                result, 
                context, 
                chatid, 
                message_thread_id, 
                messageid
            )
            
            # 如果处理后的结果为空字符串，说明消息已经被拆分发送，删除原始"思考中"消息
            if processed_result == "":
                try:
                    await context.bot.delete_message(chat_id=chatid, message_id=answer_messageid)
                    logging.info("结构化消息处理成功，已删除原始'思考中'消息")
                    return
                except Exception as e:
                    logging.error(f"删除原始消息时出错: {str(e)}")
                    return
            
            # 否则，使用处理后的结果更新原始消息
            now_result = escape(processed_result, italic=False)
            await context.bot.edit_message_text(
                chat_id=chatid, 
                message_id=answer_messageid, 
                text=now_result, 
                parse_mode='MarkdownV2', 
                disable_web_page_preview=True, 
                read_timeout=time_out, 
                write_timeout=time_out, 
                pool_timeout=time_out, 
                connect_timeout=time_out
            )
            return
        except Exception as e:
            logging.error(f"处理结构化消息时出错: {str(e)}")
            # 如果处理结构化消息失败，回退到普通消息处理
    
    # 普通消息的最终处理（非JSON或JSON处理失败的情况）
    now_result = escape(tmpresult, italic=False)
    if lastresult != now_result and answer_messageid:
        if "Can't parse entities: can't find end of code entity at byte offset" in tmpresult:
            await update_message.reply_text(tmpresult)
            print(now_result)
        elif now_result:
            try:
                await context.bot.edit_message_text(
                    chat_id=chatid, 
                    message_id=answer_messageid, 
                    text=now_result, 
                    parse_mode='MarkdownV2', 
                    disable_web_page_preview=True, 
                    read_timeout=time_out, 
                    write_timeout=time_out, 
                    pool_timeout=time_out, 
                    connect_timeout=time_out
                )
            except Exception as e:
                if "parse entities" in str(e):
                    await context.bot.edit_message_text(chat_id=chatid, message_id=answer_messageid, text=tmpresult, disable_web_page_preview=True, read_timeout=time_out, write_timeout=time_out, pool_timeout=time_out, connect_timeout=time_out)

    if Users.get_config(convo_id, "FOLLOW_UP") and tmpresult.strip():
        if title != "":
            info = "\n\n".join(tmpresult.split("\n\n")[1:])
        else:
            info = tmpresult
        prompt = (
            f"You are a professional Q&A expert. You will now be given reference information. Based on the reference information, please help me ask three most relevant questions that you most want to know from my perspective. Be concise and to the point. Do not have numbers in front of questions. Separate each question with a line break. Only output three questions in {language}, no need for any explanation. reference infomation is provided inside <infomation></infomation> XML tags."
            "Here is the reference infomation, inside <infomation></infomation> XML tags:"
            "<infomation>"
            "{}"
            "</infomation>"
        ).format(info)
        result = (await config.SummaryBot.ask_async(prompt, convo_id=convo_id, model=model_name, pass_history=0, api_url=api_url, api_key=api_key)).split('\n')
        keyboard = []
        result = [i for i in result if i.strip() and len(i) > 5]
        print(result)
        for ques in result:
            keyboard.append([KeyboardButton(ques)])
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
        await update_message.reply_text(text=escape(tmpresult, italic=False), parse_mode='MarkdownV2', reply_to_message_id=messageid, reply_markup=reply_markup)
        await context.bot.delete_message(chat_id=chatid, message_id=answer_messageid)

@decorators.AdminAuthorization
@decorators.GroupAuthorization
@decorators.Authorization
async def button_press(update, context):
    """Function to handle the button press"""
    _, _, _, _, _, _, _, _, convo_id, _, _, _ = await GetMesageInfo(update, context)
    callback_query = update.callback_query
    info_message = update_info_message(convo_id)

    await callback_query.answer()
    data = callback_query.data
    banner = strings['message_banner'][get_current_lang(convo_id)]
    import telegram
    try:
        if data.endswith("_MODELS"):
            data = data[:-7]
            Users.set_config(convo_id, "engine", data)
            try:
                info_message = update_info_message(convo_id)
                message = await callback_query.edit_message_text(
                    text=escape(info_message + banner),
                    reply_markup=InlineKeyboardMarkup(update_models_buttons(convo_id)),
                    parse_mode='MarkdownV2'
                )
            except Exception as e:
                logger.info(e)
                pass
        elif data.endswith("_GROUP"):
            # Processing a click on a group of models
            group_name = data[:-6]
            try:
                message = await callback_query.edit_message_text(
                    text=escape(info_message + f"\n\n**{strings['group_title'][get_current_lang(convo_id)]}:** `{group_name}`"),
                    reply_markup=InlineKeyboardMarkup(update_models_buttons(convo_id, group=group_name)),
                    parse_mode='MarkdownV2'
                )
            except Exception as e:
                logger.info(e)
                pass
        elif data.startswith("MODELS"):
            message = await callback_query.edit_message_text(
                text=escape(info_message + banner),
                reply_markup=InlineKeyboardMarkup(update_models_buttons(convo_id)),
                parse_mode='MarkdownV2'
            )

        elif data.endswith("_LANGUAGES"):
            data = data[:-10]
            update_language_status(data, chat_id=convo_id)
            try:
                info_message = update_info_message(convo_id)
                message = await callback_query.edit_message_text(
                    text=escape(info_message, italic=False),
                    reply_markup=InlineKeyboardMarkup(update_menu_buttons(LANGUAGES, "_LANGUAGES", convo_id)),
                    parse_mode='MarkdownV2'
                )
            except Exception as e:
                logger.info(e)
                pass
        elif data.startswith("LANGUAGE"):
            message = await callback_query.edit_message_text(
                text=escape(info_message, italic=False),
                reply_markup=InlineKeyboardMarkup(update_menu_buttons(LANGUAGES, "_LANGUAGES", convo_id)),
                parse_mode='MarkdownV2'
            )

        if data.endswith("_PREFERENCES"):
            data = data[:-12]
            try:
                current_data = Users.get_config(convo_id, data)
                if data == "PASS_HISTORY":
                    if current_data == 0:
                        current_data = config.PASS_HISTORY or 9999
                    else:
                        current_data = 0
                    Users.set_config(convo_id, data, current_data)
                else:
                    Users.set_config(convo_id, data, not current_data)
            except Exception as e:
                logger.info(e)
            try:
                info_message = update_info_message(convo_id)
                message = await callback_query.edit_message_text(
                    text=escape(info_message, italic=False),
                    reply_markup=InlineKeyboardMarkup(update_menu_buttons(PREFERENCES, "_PREFERENCES", convo_id)),
                    parse_mode='MarkdownV2'
                )
            except Exception as e:
                logger.info(e)
                pass
        elif data.startswith("PREFERENCES"):
            message = await callback_query.edit_message_text(
                text=escape(info_message, italic=False),
                reply_markup=InlineKeyboardMarkup(update_menu_buttons(PREFERENCES, "_PREFERENCES", convo_id)),
                parse_mode='MarkdownV2'
            )

        if data.endswith("_PLUGINS"):
            data = data[:-8]
            try:
                current_data = Users.get_config(convo_id, data)
                Users.set_config(convo_id, data, not current_data)
            except Exception as e:
                logger.info(e)
            try:
                info_message = update_info_message(convo_id)
                message = await callback_query.edit_message_text(
                    text=escape(info_message, italic=False),
                    reply_markup=InlineKeyboardMarkup(update_menu_buttons(PLUGINS, "_PLUGINS", convo_id)),
                    parse_mode='MarkdownV2'
                )
            except Exception as e:
                logger.info(e)
                pass
        elif data.startswith("PLUGINS"):
            message = await callback_query.edit_message_text(
                text=escape(info_message, italic=False),
                reply_markup=InlineKeyboardMarkup(update_menu_buttons(PLUGINS, "_PLUGINS", convo_id)),
                parse_mode='MarkdownV2'
            )

        elif data.startswith("BACK"):
            message = await callback_query.edit_message_text(
                text=escape(info_message, italic=False),
                reply_markup=InlineKeyboardMarkup(update_first_buttons_message(convo_id)),
                parse_mode='MarkdownV2'
            )
    except telegram.error.BadRequest as e:
        print('\033[31m')
        traceback.print_exc()
        if "Message to edit not found" in str(e):
            print("error: telegram.error.BadRequest: Message to edit not found!")
        else:
            print(f"error: {str(e)}")
        print('\033[0m')

@decorators.GroupAuthorization
@decorators.Authorization
@decorators.APICheck
async def handle_file(update, context):
    _, _, image_url, chatid, _, _, _, message_thread_id, convo_id, file_url, _, voice_text = await GetMesageInfo(update, context)
    robot, role, api_key, api_url = get_robot(convo_id)
    engine = Users.get_config(convo_id, "engine")

    if file_url == None and image_url:
        file_url = image_url
        if Users.get_config(convo_id, "IMAGEQA") == False:
            return
    if image_url == None and file_url:
        image_url = file_url
    engine_type, _ = get_engine({"base_url": api_url}, endpoint=None, original_model=engine)
    if robot.__class__.__name__ == "chatgpt":
        engine_type = "gpt"
    message = await Document_extract(file_url, image_url, engine_type)

    robot.add_to_conversation(message, role, convo_id)

    if Users.get_config(convo_id, "FILE_UPLOAD_MESS"):
        message = await context.bot.send_message(chat_id=chatid, message_thread_id=message_thread_id, text=escape(strings['message_doc'][get_current_lang(convo_id)]), parse_mode='MarkdownV2', disable_web_page_preview=True)
        await delete_message(update, context, [message.message_id])

@decorators.GroupAuthorization
@decorators.Authorization
@decorators.APICheck
async def inlinequery(update: Update, context) -> None:
    """Handle the inline query."""

    chatid = update.effective_user.id
    engine = Users.get_config(chatid, "engine")
    query = update.inline_query.query
    if (query.endswith('.') or query.endswith('。')) and query.strip():
        prompt = "Answer the following questions as concisely as possible:\n\n"
        _, _, _, chatid, _, _, _, _, convo_id, _, _, _ = await GetMesageInfo(update, context)
        robot, role, api_key, api_url = get_robot(convo_id)
        result = config.ChatGPTbot.ask(prompt + query, convo_id=convo_id, model=engine, api_url=api_url, api_key=api_key, pass_history=0)

        results = [
            InlineQueryResultArticle(
                id=chatid,
                title=f"{engine}",
                thumbnail_url="https://pb.yym68686.top/TTGk",
                description=f"{result}",
                input_message_content=InputTextMessageContent(escape(result, italic=False), parse_mode='MarkdownV2')),
        ]

        await update.inline_query.answer(results)

@decorators.GroupAuthorization
@decorators.Authorization
async def change_model(update, context):
    """Quick model change using the command"""
    _, _, _, chatid, user_message_id, _, _, message_thread_id, convo_id, _, _, _ = await GetMesageInfo(update, context)
    lang = get_current_lang(convo_id)

    if not context.args:
        message = await context.bot.send_message(
            chat_id=chatid,
            message_thread_id=message_thread_id,
            text=escape(strings['model_command_usage'][lang]),
            parse_mode='MarkdownV2',
            reply_to_message_id=user_message_id,
        )
        return

    # Combine all arguments into one model name
    model_name = ' '.join(context.args)

    # Check if the model name is valid (allowing all common model name characters)
    if not re.match(r'^[a-zA-Z0-9\-_\./:\\@+\s]+$', model_name) or len(model_name) > 100:
        message = await context.bot.send_message(
            chat_id=chatid,
            message_thread_id=message_thread_id,
            text=escape(strings['model_name_invalid'][lang]),
            parse_mode='MarkdownV2',
            reply_to_message_id=user_message_id,
        )
        return

    # Get all available models from initial_model and MODEL_GROUPS
    available_models = get_all_available_models()
    for group_name, models in get_model_groups().items():
        available_models.extend(models)

    # Add debug output
    print(f"Requested model: '{model_name}'")
    print(f"Available models: {available_models}")

    # Check if the requested model is in the available models list
    if model_name not in available_models:
        message = await context.bot.send_message(
            chat_id=chatid,
            message_thread_id=message_thread_id,
            text=escape(strings['model_not_available'][lang].format(model_name=model_name)),
            parse_mode='MarkdownV2',
            reply_to_message_id=user_message_id,
        )
        return

    # Saving the new model in the user's configuration
    Users.set_config(convo_id, "engine", model_name)

    # Sending a message about changing the model
    message = await context.bot.send_message(
        chat_id=chatid,
        message_thread_id=message_thread_id,
        text=escape(strings['model_changed'][lang].format(model_name=model_name), italic=False),
        parse_mode='MarkdownV2',
        reply_to_message_id=user_message_id,
    )

async def scheduled_function(context: ContextTypes.DEFAULT_TYPE) -> None:
    """这个函数将在RESET_TIME秒后执行一次，重置特定用户的对话"""
    job = context.job
    chat_id = job.chat_id

    if config.ADMIN_LIST and chat_id in config.ADMIN_LIST:
        return

    reset_ENGINE(chat_id)

    # 任务执行完毕后自动移除
    remove_job_if_exists(str(chat_id), context)

def remove_job_if_exists(name: str, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """如果存在，则移除指定名称的任务"""
    current_jobs = context.job_queue.get_jobs_by_name(name)
    if not current_jobs:
        return False
    for job in current_jobs:
        job.schedule_removal()
    return True

# 定义一个全局变量来存储 chatid
target_convo_id = None
reset_mess_id = 9999

@decorators.GroupAuthorization
@decorators.Authorization
async def reset_chat(update, context):
    global target_convo_id, reset_mess_id
    _, _, _, chatid, user_message_id, _, _, message_thread_id, convo_id, _, _, _ = await GetMesageInfo(update, context)
    reset_mess_id = user_message_id
    target_convo_id = convo_id
    stop_event.set()
    message = None
    if (len(context.args) > 0):
        message = ' '.join(context.args)
    reset_ENGINE(target_convo_id, message)

    remove_keyboard = ReplyKeyboardRemove()
    message = await context.bot.send_message(
        chat_id=chatid,
        message_thread_id=message_thread_id,
        text=escape(strings['message_reset'][get_current_lang(convo_id)]),
        reply_markup=remove_keyboard,
        parse_mode='MarkdownV2',
    )
    if GET_MODELS:
        robot, role, api_key, api_url = get_robot()
        engine = Users.get_config(convo_id, "engine")
        provider = {
            "provider": "openai",
            "base_url": api_url,
            "api": api_key,
            "model": [engine],
            "tools": True,
            "image": True
        }
        config.initial_model = remove_no_text_model(update_initial_model(provider))
    await delete_message(update, context, [message.message_id, user_message_id])

@decorators.AdminAuthorization
@decorators.GroupAuthorization
@decorators.Authorization
async def info(update, context):
    _, _, _, chatid, user_message_id, _, _, message_thread_id, convo_id, _, _, voice_text = await GetMesageInfo(update, context)
    info_message = update_info_message(convo_id)
    message = await context.bot.send_message(
        chat_id=chatid,
        message_thread_id=message_thread_id,
        text=escape(info_message, italic=False),
        reply_markup=InlineKeyboardMarkup(update_first_buttons_message(convo_id)),
        parse_mode='MarkdownV2',
        disable_web_page_preview=True,
        read_timeout=600,
    )
    await delete_message(update, context, [message.message_id, user_message_id])

@decorators.PrintMessage
@decorators.GroupAuthorization
@decorators.Authorization
async def start(update, context): # 当用户输入/start时，返回文本
    _, _, _, _, _, _, _, _, convo_id, _, _, _ = await GetMesageInfo(update, context)
    user = update.effective_user
    if user.language_code == "zh-hans":
        update_language_status("Simplified Chinese", chat_id=convo_id)
    elif user.language_code == "zh-hant":
        update_language_status("Traditional Chinese", chat_id=convo_id)
    elif user.language_code == "ru":
        update_language_status("Russian", chat_id=convo_id)
    else:
        update_language_status("English", chat_id=convo_id)
    message = (
        f"Hi `{user.username}` ! I am an Assistant, a large language model trained by OpenAI. I will do my best to help answer your questions.\n\n"
    )
    if len(context.args) == 2 and context.args[1].startswith("sk-"):
        api_url = context.args[0]
        api_key = context.args[1]
        Users.set_config(convo_id, "api_key", api_key)
        Users.set_config(convo_id, "api_url", api_url)
        # if GET_MODELS:
        #     update_initial_model()

    if len(context.args) == 1 and context.args[0].startswith("sk-"):
        api_key = context.args[0]
        Users.set_config(convo_id, "api_key", api_key)
        Users.set_config(convo_id, "api_url", "https://api.openai.com/v1/chat/completions")
        # if GET_MODELS:
        #     update_initial_model()

    # message = (
    #     ">Block quotation started\n"
    #     ">Block quotation continued\n"
    #     ">Block quotation continued\n"
    #     ">Block quotation continued\n"
    #     ">The last line of the block quotation\n"
    #     "**>The expandable block quotation started right after the previous block quotation\n"
    #     ">It is separated from the previous block quotation by an empty bold entity\n"
    #     ">Expandable block quotation continued\n"
    #     ">Hidden by default part of the expandable block quotation started\n"
    #     ">Expandable block quotation continued\n"
    #     ">The last line of the expandable block quotation with the expandability mark||\n"
    # )
    # await update.message.reply_text(message, parse_mode='MarkdownV2', disable_web_page_preview=True)
    await update.message.reply_text(escape(message, italic=False), parse_mode='MarkdownV2', disable_web_page_preview=True)

async def error(update, context):
    traceback_string = traceback.format_exception(None, context.error, context.error.__traceback__)
    if "telegram.error.TimedOut: Timed out" in traceback_string:
        logger.warning('error: telegram.error.TimedOut: Timed out')
        return
    if "Message to be replied not found" in traceback_string:
        logger.warning('error: telegram.error.BadRequest: Message to be replied not found')
        return
    logger.warning('Update "%s" caused error "%s"', update, context.error)
    logger.warning('Error traceback: %s', ''.join(traceback_string))

@decorators.GroupAuthorization
@decorators.Authorization
async def unknown(update, context): # 当用户输入未知命令时，返回文本
    return
    # await context.bot.send_message(chat_id=update.effective_chat.id, text="Sorry, I didn't understand that command.")

async def post_init(application: Application) -> None:
    await application.bot.set_my_commands([
        BotCommand('info', '基本信息'),
        BotCommand('reset', '重置机器人'),
        BotCommand('start', '启动机器人'),
        BotCommand('model', '切换AI模型'),
        BotCommand('plan_messages', '规划主动消息'),
        BotCommand('test_message', '发送测试主动消息'),
        BotCommand('view_messages', '查看已规划消息'),
        BotCommand('remember', '记住一条信息'),
        BotCommand('memories', '列出所有记忆'),
        BotCommand('forget', '遗忘一条记忆'),
        BotCommand('summarize_memory', '总结当前对话记忆'),
        BotCommand('clear_history', '清除对话历史'),
    ])
    description = (
        "I am an Assistant, a large language model trained by OpenAI. I will do my best to help answer your questions."
    )
    await application.bot.set_my_description(description)

@decorators.GroupAuthorization
@decorators.Authorization
@decorators.APICheck
async def plan_messages(update, context):
    """手动触发消息规划"""
    if not proactive_messaging.PROACTIVE_AGENT_ENABLED:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="主动消息功能未启用")
        return
    result = await proactive_messaging.trigger_message_planning(context)
    await context.bot.send_message(chat_id=update.effective_chat.id, text=result)

@decorators.GroupAuthorization
@decorators.Authorization
@decorators.APICheck
async def test_message(update, context):
    """发送测试消息"""
    if not proactive_messaging.PROACTIVE_AGENT_ENABLED:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="主动消息功能未启用")
        return
    result = await proactive_messaging.send_test_message(context, str(update.effective_chat.id))
    await context.bot.send_message(chat_id=update.effective_chat.id, text=result)

@decorators.GroupAuthorization
@decorators.Authorization
@decorators.APICheck
async def view_messages(update, context):
    """查看当前已计划的触发器"""
    if not proactive_messaging.PROACTIVE_AGENT_ENABLED:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="主动消息功能未启用")
        return
    result = await proactive_messaging.view_planned_messages()
    await context.bot.send_message(chat_id=update.effective_chat.id, text=result)

@decorators.GroupAuthorization
@decorators.Authorization
@decorators.APICheck
async def remember(update, context):
    """记住指定的信息"""
    if not context.args or len(context.args) == 0:
        usage = "使用方法: /remember [要记住的内容]\n\n例如:\n/remember 我喜欢吃巧克力\n/remember 我的生日是5月12日"
        await context.bot.send_message(chat_id=update.effective_chat.id, text=usage)
        return
    
    content = " ".join(context.args)
    user_id = str(update.effective_chat.id)
    
    # 尝试添加到记忆
    success = await add_explicit_memory(user_id, content, importance=4)
    
    if success:
        await context.bot.send_message(
            chat_id=update.effective_chat.id, 
            text=f"我已经记住了：'{content}'\n\n这条记忆将会影响我们未来的对话。"
        )
    else:
        await context.bot.send_message(
            chat_id=update.effective_chat.id, 
            text="很抱歉，我无法保存这条记忆。请稍后再试。"
        )

@decorators.GroupAuthorization
@decorators.Authorization
@decorators.APICheck
async def memories(update, context):
    """列出所有记忆"""
    user_id = str(update.effective_chat.id)
    
    # 获取记忆列表
    memory_list = await list_memories(user_id, max_count=15)
    
    await context.bot.send_message(
        chat_id=update.effective_chat.id, 
        text=memory_list
    )

@decorators.GroupAuthorization
@decorators.Authorization
@decorators.APICheck
async def forget(update, context):
    """忘记特定的记忆"""
    if not context.args or len(context.args) == 0:
        usage = "使用方法: /forget [记忆ID]\n\n例如:\n/forget 1\n\n使用 /memories 命令查看所有记忆及其ID。"
        await context.bot.send_message(chat_id=update.effective_chat.id, text=usage)
        return
    
    try:
        memory_id = int(context.args[0])
        user_id = str(update.effective_chat.id)
        
        # 尝试删除记忆
        success = await forget_memory(user_id, memory_id)
        
        if success:
            await context.bot.send_message(
                chat_id=update.effective_chat.id, 
                text=f"我已经忘记了ID为 {memory_id} 的记忆。"
            )
        else:
            await context.bot.send_message(
                chat_id=update.effective_chat.id, 
                text=f"我找不到ID为 {memory_id} 的记忆，或者删除失败。"
            )
    except ValueError:
        await context.bot.send_message(
            chat_id=update.effective_chat.id, 
            text="记忆ID必须是一个数字。使用 /memories 命令查看所有记忆及其ID。"
        )

@decorators.GroupAuthorization
@decorators.Authorization
@decorators.APICheck
async def forget_batch(update, context):
    """批量忘记多个记忆"""
    if not context.args or len(context.args) == 0:
        usage = "使用方法: /forget_batch [记忆ID1] [记忆ID2] ...\n\n例如:\n/forget_batch 1 3 5\n\n使用 /memories 命令查看所有记忆及其ID。"
        await context.bot.send_message(chat_id=update.effective_chat.id, text=usage)
        return
    
    user_id = str(update.effective_chat.id)
    memory_ids = []
    invalid_ids = []
    
    # 处理所有参数，转换为整数ID
    for arg in context.args:
        try:
            memory_id = int(arg)
            memory_ids.append(memory_id)
        except ValueError:
            invalid_ids.append(arg)
    
    # 如果有无效ID，提示用户
    if invalid_ids:
        await context.bot.send_message(
            chat_id=update.effective_chat.id, 
            text=f"以下ID无效（必须是数字）: {', '.join(invalid_ids)}"
        )
        if not memory_ids:  # 如果没有有效ID，直接返回
            return
    
    # 如果没有有效ID，直接返回
    if not memory_ids:
        await context.bot.send_message(
            chat_id=update.effective_chat.id, 
            text="没有提供有效的记忆ID。使用 /memories 命令查看所有记忆及其ID。"
        )
        return
    
    # 发送处理中消息
    processing_message = await context.bot.send_message(
        chat_id=update.effective_chat.id, 
        text=f"正在删除 {len(memory_ids)} 条记忆..."
    )
    
    # 从memory_integration.py导入forget_memories函数
    from utils.memory_integration import forget_memories
    
    # 尝试批量删除记忆
    results = await forget_memories(user_id, memory_ids)
    
    # 构建结果消息
    if results["success"]:
        success_text = f"已成功删除 {len(results['success'])} 条记忆（ID: {', '.join(map(str, results['success']))}）"
    else:
        success_text = "没有成功删除任何记忆"
    
    if results["failed"]:
        failed_text = f"删除失败 {len(results['failed'])} 条记忆（ID: {', '.join(map(str, results['failed']))}）"
        result_message = f"{success_text}\n{failed_text}"
    else:
        result_message = success_text
    
    # 更新处理中消息为结果消息
    await context.bot.edit_message_text(
        chat_id=update.effective_chat.id,
        message_id=processing_message.message_id,
        text=result_message
    )

@decorators.GroupAuthorization
@decorators.Authorization
@decorators.APICheck
async def summarize_memory(update, context):
    """强制总结当前对话记忆"""
    chatid = update.effective_chat.id
    user_id = str(chatid)
    
    # 发送处理中消息
    processing_message = await context.bot.send_message(
        chat_id=chatid,
        text="正在使用Gemini Flash总结当前对话历史并提取记忆，请稍候..."
    )
    
    # 获取机器人实例
    robot = config.ChatGPTbot
    if not robot:
        await context.bot.edit_message_text(
            chat_id=chatid,
            message_id=processing_message.message_id,
            text="无法获取AI模型实例，请稍后再试。"
        )
        return
    
    # 执行强制总结
    result = await force_summarize_memory(user_id, robot)
    
    # 发送结果
    await context.bot.edit_message_text(
        chat_id=chatid,
        message_id=processing_message.message_id,
        text=result
    )

@decorators.GroupAuthorization
@decorators.Authorization
@decorators.APICheck
async def clear_history(update, context):
    """清除用户的对话历史"""
    await proactive_messaging.clear_conversation_history(update, context)

if __name__ == '__main__':
    application = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .concurrent_updates(True)
        .connection_pool_size(65536)
        .get_updates_connection_pool_size(65536)
        .read_timeout(time_out)
        .write_timeout(time_out)
        .connect_timeout(time_out)
        .pool_timeout(time_out)
        .get_updates_read_timeout(time_out)
        .get_updates_write_timeout(time_out)
        .get_updates_connect_timeout(time_out)
        .get_updates_pool_timeout(time_out)
        .rate_limiter(AIORateLimiter(max_retries=5))
        .post_init(post_init)
        .build()
    )

    application.add_handler(CommandHandler("info", info))
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("reset", reset_chat))
    application.add_handler(CommandHandler("model", change_model))
    application.add_handler(CommandHandler("plan_messages", plan_messages))
    application.add_handler(CommandHandler("test_message", test_message))
    application.add_handler(CommandHandler("view_messages", view_messages))
    application.add_handler(CommandHandler("remember", remember))
    application.add_handler(CommandHandler("memories", memories))
    application.add_handler(CommandHandler("forget", forget))
    application.add_handler(CommandHandler("forget_batch", forget_batch))
    application.add_handler(CommandHandler("summarize_memory", summarize_memory))
    application.add_handler(CommandHandler("clear_history", clear_history))
    
    # 新的基于function calling的记忆系统命令
    application.add_handler(CommandHandler("new_memories", list_new_memories))
    application.add_handler(CommandHandler("new_memory", add_new_memory))
    application.add_handler(CommandHandler("forget_new", delete_new_memory))
    application.add_handler(InlineQueryHandler(inlinequery))
    application.add_handler(CallbackQueryHandler(button_press))
    application.add_handler(MessageHandler((filters.TEXT | filters.VOICE) & ~filters.COMMAND, lambda update, context: command_bot(update, context, prompt=None, has_command=False), block = False))
    application.add_handler(MessageHandler(
        filters.CAPTION &
        (
            (filters.PHOTO & ~filters.COMMAND) |
            (
                filters.Document.PDF |
                filters.Document.TXT |
                filters.Document.DOC |
                filters.Document.FileExtension("jpg") |
                filters.Document.FileExtension("jpeg") |
                filters.Document.FileExtension("png") |
                filters.Document.FileExtension("md") |
                filters.Document.FileExtension("py") |
                filters.Document.FileExtension("yml")
            )
        ), lambda update, context: command_bot(update, context, prompt=None, has_command=False)))
    application.add_handler(MessageHandler(
        ~filters.CAPTION &
        (
            (filters.PHOTO & ~filters.COMMAND) |
            (
                filters.Document.PDF |
                filters.Document.TXT |
                filters.Document.DOC |
                filters.Document.FileExtension("jpg") |
                filters.Document.FileExtension("jpeg") |
                filters.Document.FileExtension("png") |
                filters.Document.FileExtension("md") |
                filters.Document.FileExtension("py") |
                filters.Document.FileExtension("yml") |
                filters.AUDIO |
                filters.Document.FileExtension("wav")
            )
        ), handle_file))
    application.add_handler(MessageHandler(filters.COMMAND, unknown))
    application.add_error_handler(error)

    # 初始化主动消息功能
    proactive_messaging.init_proactive_messaging(application)
    
    if WEB_HOOK:
        print("WEB_HOOK:", WEB_HOOK)
        application.run_webhook("0.0.0.0", PORT, webhook_url=WEB_HOOK)
    else:
        application.run_polling(timeout=time_out)
