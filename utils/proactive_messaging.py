import os
import json
import logging
import random
from datetime import datetime, timedelta
import asyncio
import re
import datetime as dt
import pytz  # 添加pytz库用于时区转换
import traceback  # 添加traceback模块用于详细错误信息

from telegram.ext import ContextTypes
from config import Users, get_robot, GOOGLE_AI_API_KEY, ChatGPTbot
from utils.message_splitter import process_structured_messages

# 配置项
PROACTIVE_AGENT_ENABLED = os.environ.get('PROACTIVE_AGENT_ENABLED', 'false').lower() == 'true'
PROACTIVE_AGENT_MODEL = os.environ.get('PROACTIVE_AGENT_MODEL', 'gemini-2.5-flash-preview-04-17')
ADMIN_LIST = os.environ.get('ADMIN_LIST', '')

# 连续对话配置
MAX_CONTINUOUS_MESSAGES = int(os.environ.get('MAX_CONTINUOUS_MESSAGES', '2'))  # 最大连续消息数量，默认改为2
CONTINUOUS_MESSAGE_DELAY = int(os.environ.get('CONTINUOUS_MESSAGE_DELAY', '30'))  # 连续消息之间的延迟（秒）

# 添加：用户最后对话时间（用户ID -> 最后对话时间）
last_user_chat_time = {}

# 定义东八区时区
CHINA_TZ = pytz.timezone('Asia/Shanghai')

# 获取当前东八区时间
def get_china_time():
    """获取当前东八区时间"""
    return datetime.now(CHINA_TZ)

# 更新用户最后对话时间
def update_last_chat_time(user_id):
    """更新用户最后对话时间"""
    last_user_chat_time[user_id] = get_china_time()
    logging.info(f"更新用户 {user_id} 的最后对话时间为 {last_user_chat_time[user_id]}")

# 检查是否应该发送主动消息
async def check_proactive_desire(context: ContextTypes.DEFAULT_TYPE):
    """定期检查所有用户，让模型自主决定是否发送主动消息"""
    if not PROACTIVE_AGENT_ENABLED:
        return
    
    try:
        # 获取管理员ID列表
        admin_ids = get_admin_ids()
        if not admin_ids:
            return
        
        # 获取当前时间
        current_time = get_china_time()
        current_hour = current_time.hour
        
        # 如果当前时间不在7-24点之间，不执行检查
        if current_hour < 7 or current_hour > 24:
            logging.info(f"当前时间 {current_hour}点 不在主动消息时间范围内，跳过检查")
            return
        
        logging.info(f"开始检查主动对话，当前时间: {current_time.strftime('%Y-%m-%d %H:%M:%S')}")
        
        # 遍历所有用户
        for user_id in admin_ids:
            try:
                # 获取机器人实例和相关配置
                robot, _, api_key, api_url = get_robot(str(user_id))
                main_convo_id = str(user_id)
                
                # 检查是否有对话历史
                if main_convo_id in robot.conversation and len(robot.conversation[main_convo_id]) >= 1:
                    # 获取最后一条消息
                    last_message = robot.conversation[main_convo_id][-1]
                    
                    # 如果最后一条是用户消息，且不是系统添加的虚拟消息，说明用户正在等待回复
                    if (last_message.get("role") == "user" and 
                        "我想和你聊聊天" not in last_message.get("content", "") and
                        "我想继续和你聊天" not in last_message.get("content", "")):
                        logging.info(f"用户 {user_id} 正在等待回复，跳过主动消息")
                        continue
                
                # 检查连续主动消息数量
                continuous_bot_messages = 0
                if main_convo_id in robot.conversation:
                    # 从最后一条消息开始向前检查
                    for msg in reversed(robot.conversation[main_convo_id]):
                        # 如果遇到用户消息，停止计数
                        if msg.get("role") == "user" and "我想和你聊聊天" not in msg.get("content", "") and "我想继续和你聊天" not in msg.get("content", ""):
                            break
                        # 如果是机器人消息，增加计数
                        if msg.get("role") == "assistant":
                            continuous_bot_messages += 1
                    
                    # 如果已经有两条或更多连续机器人消息，跳过发送
                    if continuous_bot_messages >= MAX_CONTINUOUS_MESSAGES:
                        logging.info(f"用户 {user_id} 已有 {continuous_bot_messages} 条连续机器人消息未回复，跳过主动消息")
                        continue
                
                # 获取上次发送主动消息的时间
                last_proactive_time = getattr(robot, 'last_proactive_time', {}).get(user_id, datetime.fromtimestamp(0))
                
                # 确保 last_proactive_time 有时区信息
                if last_proactive_time.tzinfo is None:
                    # 如果没有时区信息，添加东八区时区
                    last_proactive_time = CHINA_TZ.localize(last_proactive_time)
                
                # 计算距离上次主动消息的时间（小时）
                hours_since_last_proactive = (current_time - last_proactive_time).total_seconds() / 3600
                
                # 如果距离上次主动消息不足2小时，跳过检查
                if hours_since_last_proactive < 2:
                    logging.info(f"距离上次主动消息仅 {hours_since_last_proactive:.1f} 小时，跳过检查")
                    continue
                
                # 获取上次用户对话时间
                last_chat = last_user_chat_time.get(user_id, current_time - timedelta(hours=24))
                
                # 确保 last_chat 有时区信息
                if last_chat.tzinfo is None:
                    # 如果没有时区信息，添加东八区时区
                    last_chat = CHINA_TZ.localize(last_chat)
                
                # 计算距离上次用户对话的时间（小时）
                hours_since_last_chat = (current_time - last_chat).total_seconds() / 3600
                
                # 获取系统提示词
                system_prompt = Users.get_config(str(user_id), "systemprompt")
                
                # 添加当前东八区日期和时间
                current_datetime = datetime.now(CHINA_TZ)
                current_date = current_datetime.strftime("%Y-%m-%d")
                current_time_str = current_datetime.strftime("%H:%M")
                
                # 构建特殊的系统提示词，让模型自主决定是否发送主动消息
                decision_prompt = f"""当前日期和时间（东八区）：{current_date} {current_time_str}

{system_prompt}

你现在需要决定是否要主动给用户发送一条消息。请考虑以下因素：
1. 当前时间是否适合打扰用户
2. 距离上次对话的时间长短（已经过去了 {hours_since_last_chat:.1f} 小时）
3. 是否有有价值的内容可以分享

请只回复 JSON 格式：
```json
{{
  "decision": true/false,  // 是否要发送主动消息
  "reason": "你的决定理由",  // 简短说明为什么做出这个决定
  "message": "如果决定发送，这里是消息内容"  // 如果决定发送，这里填写要发送的消息内容
}}
```

注意：如果决定不发送消息，message字段可以留空。如果决定发送，请确保message字段包含有意义的内容。"""
                
                # 调用AI获取决策
                model = os.environ.get('PROACTIVE_AGENT_MODEL', 'gemini-2.5-flash-preview-04-17')
                decision_response = await get_ai_response(user_id, "请决定是否要发送主动消息", decision_prompt, save_to_history=False, model=model)
                
                if not decision_response:
                    logging.error(f"无法为用户 {user_id} 获取主动消息决策")
                    continue
                
                # 尝试解析JSON响应
                try:
                    # 尝试提取JSON部分（可能包含在代码块中）
                    json_match = re.search(r'```(?:json)?\s*({[\s\S]*?})\s*```', decision_response)
                    if json_match:
                        decision_json = json.loads(json_match.group(1))
                    else:
                        # 尝试直接解析整个响应
                        decision_json = json.loads(decision_response)
                    
                    # 获取决策
                    should_send = decision_json.get("decision", False)
                    reason = decision_json.get("reason", "未提供理由")
                    message_content = decision_json.get("message", "")
                    
                    logging.info(f"AI决策: 是否发送主动消息 = {should_send}, 理由: {reason}")
                    
                    # 如果决定发送消息
                    if should_send and message_content:
                        # 处理结构化消息，检查是否需要拆分发送
                        processed_result = await process_structured_messages(
                            message_content, 
                            context, 
                            user_id
                        )
                        
                        # 如果处理后的结果不为空字符串，说明消息没有被拆分发送，使用普通方式发送
                        if processed_result != "":
                            await context.bot.send_message(chat_id=user_id, text=processed_result)
                        
                        # 将消息保存到对话历史
                        if main_convo_id in robot.conversation:
                            # 添加虚拟的用户消息，表示用户想聊天（但不会显示给用户）
                            robot.add_to_conversation({"role": "user", "content": "我想和你聊聊天"}, main_convo_id)
                            # 添加机器人的回复，并包含时间戳
                            robot.add_to_conversation({
                                "role": "assistant", 
                                "content": message_content,
                                "timestamp": str(current_datetime.timestamp())
                            }, main_convo_id)
                            logging.info(f"已发送主动消息给用户 {user_id} 并加入到主对话历史")
                        
                        # 记录本次主动消息时间
                        if not hasattr(robot, 'last_proactive_time'):
                            robot.last_proactive_time = {}
                        robot.last_proactive_time[user_id] = current_datetime
                        
                        # 设置检查用户回复的定时任务
                        # 如果用户在一定时间内没有回复，可能会发送后续消息
                        job_name = f"check_response_{user_id}"
                        remove_job_if_exists(job_name, context)
                        context.job_queue.run_once(
                            lambda ctx: asyncio.create_task(check_user_response(ctx, user_id)),
                            CONTINUOUS_MESSAGE_DELAY,
                            name=job_name
                        )
                    else:
                        logging.info(f"AI决定不发送主动消息给用户 {user_id}: {reason}")
                
                except Exception as e:
                    logging.error(f"解析AI决策时出错: {str(e)}")
                    traceback.print_exc()
                
            except Exception as e:
                logging.error(f"为用户 {user_id} 处理主动消息时出错: {str(e)}")
                traceback.print_exc()
                
    except Exception as e:
        logging.error(f"检查主动对话时出错: {str(e)}")
        traceback.print_exc()

# 获取管理员ID列表
def get_admin_ids():
    """获取管理员ID列表"""
    if not ADMIN_LIST:
        return []
    
    return [admin_id.strip() for admin_id in ADMIN_LIST.split(',') if admin_id.strip()]

# 移除指定的任务
def remove_job_if_exists(name, context):
    """如果存在，则移除指定名称的任务"""
    current_jobs = context.job_queue.get_jobs_by_name(name)
    if not current_jobs:
        return False
    for job in current_jobs:
        job.schedule_removal()
    return True

# 发送主动消息
async def send_proactive_message(context: ContextTypes.DEFAULT_TYPE, user_id: str, reason: str):
    """发送主动消息给用户"""
    try:
        # 获取机器人实例和相关配置
        robot, _, api_key, api_url = get_robot(str(user_id))
        
        # 获取系统提示词
        system_prompt = Users.get_config(str(user_id), "systemprompt")
        
        # 添加当前东八区日期和时间
        current_datetime = datetime.now(CHINA_TZ)
        current_date = current_datetime.strftime("%Y-%m-%d")
        current_time = current_datetime.strftime("%H:%M")
        system_prompt = f"当前日期和时间（东八区）：{current_date} {current_time}\n\n{system_prompt}"
        
        # 生成消息内容
        model = os.environ.get('PROACTIVE_AGENT_MODEL', 'gemini-2.5-flash-preview-04-17')
        message_content = await generate_message_content(user_id, reason, system_prompt, save_to_history=False, model=model)
        
        if not message_content:
            logging.error(f"无法为用户 {user_id} 生成主动消息")
            return
        
        # 处理结构化消息，检查是否需要拆分发送
        processed_result = await process_structured_messages(
            message_content, 
            context, 
            user_id
        )
        
        # 如果处理后的结果不为空字符串，说明消息没有被拆分发送，使用普通方式发送
        if processed_result != "":
            await context.bot.send_message(chat_id=user_id, text=processed_result)
        
        # 将消息保存到对话历史
        main_convo_id = str(user_id)
        if main_convo_id in robot.conversation:
            # 添加虚拟的用户消息，表示用户想聊天（但不会显示给用户）
            robot.add_to_conversation({"role": "user", "content": "我想和你聊聊天"}, main_convo_id)
            # 添加机器人的回复，并包含时间戳
            robot.add_to_conversation({
                "role": "assistant", 
                "content": message_content,
                "timestamp": str(current_datetime.timestamp())
            }, main_convo_id)
            logging.info(f"已发送主动消息给用户 {user_id} 并加入到主对话历史")
        
        # 记录本次主动消息时间
        if not hasattr(robot, 'last_proactive_time'):
            robot.last_proactive_time = {}
        robot.last_proactive_time[user_id] = current_datetime
        
        # 设置检查用户回复的定时任务
        job_id = f"check_response_{user_id}"
        context.job_queue.run_once(
            lambda ctx: asyncio.create_task(check_user_response(ctx, user_id)),
            30,
            name=job_id
        )
        
    except Exception as e:
        logging.error(f"发送主动消息给用户 {user_id} 时出错: {str(e)}")
        traceback.print_exc()

# 检查用户是否回复
async def check_user_response(context: ContextTypes.DEFAULT_TYPE, user_id: str):
    """检查用户是否回复了主动消息，如果没有，可能发送后续消息"""
    try:
        # 获取机器人实例
        robot, _, api_key, api_url = get_robot(str(user_id))
        main_convo_id = str(user_id)
        
        # 检查用户是否已回复
        last_user_message_time = None
        last_bot_message_time = None
        last_bot_message = None
        
        if main_convo_id in robot.conversation:
            # 过滤掉系统消息和特殊指令
            filtered_messages = []
            for msg in robot.conversation[main_convo_id]:
                role = msg.get("role", "")
                content = msg.get("content", "")
                
                # 跳过系统消息
                if role == "system":
                    continue
                
                # 跳过特殊指令
                if role == "user" and (
                    content.startswith("/") or 
                    "我想和你聊聊天" in content or 
                    "我想继续和你聊天" in content
                ):
                    continue
                
                filtered_messages.append(msg)
            
            # 检查最后的消息
            if filtered_messages:
                # 获取最后一条消息的角色
                last_message_role = filtered_messages[-1].get("role", "")
                
                # 如果最后一条是机器人消息，说明用户还没有回复
                if last_message_role == "assistant":
                    # 获取最后一条机器人消息的时间
                    last_message_time = None
                    for msg in reversed(filtered_messages):
                        if msg.get("role") == "assistant":
                            last_message_time = msg.get("timestamp")
                            last_bot_message = msg.get("content", "")
                            break
                    
                    # 获取最后一条用户消息的时间
                    for msg in reversed(filtered_messages):
                        if msg.get("role") == "user":
                            last_user_message_time = msg.get("timestamp")
                            break
                    
                    # 如果找到了最后一条机器人消息的时间
                    if last_message_time:
                        # 计算时间差（分钟）
                        current_time = get_china_time()
                        last_message_datetime = datetime.fromtimestamp(last_message_time, CHINA_TZ)
                        time_diff = (current_time - last_message_datetime).total_seconds() / 60
                        
                        logging.info(f"用户 {user_id} 的最后一条机器人消息发送于 {time_diff:.1f} 分钟前")
                        
                        # 如果时间差超过阈值且未超过最大连续消息数量，发送后续消息
                        # 获取已发送的连续消息数量
                        continuous_count = 0
                        for msg in reversed(filtered_messages):
                            if msg.get("role") == "user":
                                break
                            if msg.get("role") == "assistant":
                                continuous_count += 1
                        
                        # 严格限制连续消息数量，确保不超过MAX_CONTINUOUS_MESSAGES
                        if time_diff >= 2 and continuous_count < MAX_CONTINUOUS_MESSAGES:
                            # 再次检查，确保不会超过限制
                            if continuous_count >= MAX_CONTINUOUS_MESSAGES - 1:
                                logging.info(f"用户 {user_id} 已达到最大连续消息数量 {MAX_CONTINUOUS_MESSAGES}，不再发送后续消息")
                                return
                            
                            # 生成后续消息
                            logging.info(f"用户 {user_id} 在 {time_diff:.1f} 分钟内没有回复，尝试发送后续消息")
                            
                            # 提取最近的对话历史
                            recent_history = ""
                            for msg in filtered_messages[-10:]:
                                role_text = "用户" if msg.get("role") == "user" else "助手"
                                content = msg.get("content", "").strip()
                                if content:
                                    recent_history += f"{role_text}: {content}\n\n"
                            
                            # 构建API格式的历史记录（用于传递给模型）
                            conversation_history = [
                                {"role": msg.get("role"), "content": msg.get("content")}
                                for msg in filtered_messages[-10:]
                            ]
                            
                            # 构建提示词
                            prompt = f"""
                            我注意到用户在我上一条消息后没有回复。作为一个体贴的AI助手，我想发送一条后续消息来继续对话。

                            请根据我们之前的对话历史，生成一条自然、有吸引力的后续消息。这条消息应该：
                            1. 与我们之前的对话主题相关
                            2. 展示出我在倾听并理解用户
                            3. 可能提出一个相关的问题或分享一个相关的想法
                            4. 不要显得太过急切或打扰用户

                            最近的对话历史：
                            {recent_history}

                            我的上一条消息是：
                            {last_bot_message}

                            请生成一条自然的后续消息，保持对话的连贯性和吸引力。
                            """
                            
                            # 获取系统提示词
                            system_prompt = Users.get_config(str(user_id), "systemprompt")
                            
                            # 调用AI获取响应，传递对话历史
                            response = await get_ai_response(
                                user_id=user_id,
                                message=prompt,
                                system_prompt=system_prompt,
                                save_to_history=False,  # 不保存这个提示到历史记录
                                model=PROACTIVE_AGENT_MODEL,
                                conversation_history=conversation_history
                            )
                            
                            # 确保响应不为空
                            if response and response.strip():
                                # 处理结构化消息，检查是否需要拆分发送
                                processed_result = await process_structured_messages(
                                    response, 
                                    context, 
                                    user_id
                                )
                                
                                # 如果处理后的结果不为空字符串，说明消息没有被拆分发送，使用普通方式发送
                                if processed_result != "":
                                    # 发送后续消息
                                    await context.bot.send_message(chat_id=user_id, text=processed_result)
                                
                                # 将后续消息保存到对话历史
                                if main_convo_id in robot.conversation:
                                    robot.conversation[main_convo_id].append({
                                        "role": "assistant",
                                        "content": response,
                                        "timestamp": datetime.now(CHINA_TZ).timestamp()
                                    })
                                
                                logging.info(f"已向用户 {user_id} 发送后续消息")
                                
                                # 如果还没有达到最大连续消息数量，设置下一次检查
                                if continuous_count + 1 < MAX_CONTINUOUS_MESSAGES:
                                    context.job_queue.run_once(
                                        lambda ctx: asyncio.ensure_future(check_user_response(ctx, user_id)),
                                        when=timedelta(seconds=CONTINUOUS_MESSAGE_DELAY),  # 延迟后再次检查
                                        name=f"check_response_{user_id}"
                                    )
                                    
                                    logging.info(f"将在 {CONTINUOUS_MESSAGE_DELAY} 秒后再次检查用户 {user_id} 的回复")
                            else:
                                logging.warning(f"为用户 {user_id} 生成后续消息失败，内容为空")
                        else:
                            if continuous_count >= MAX_CONTINUOUS_MESSAGES:
                                logging.info(f"用户 {user_id} 已达到最大连续消息数量 {MAX_CONTINUOUS_MESSAGES}，不再发送后续消息")
                            else:
                                logging.info(f"用户 {user_id} 的最后一条消息发送时间未超过阈值，不发送后续消息")
                else:
                    logging.info(f"用户 {user_id} 已回复，不需要发送后续消息")
            else:
                logging.info(f"用户 {user_id} 没有有效的对话历史")
        else:
            logging.info(f"用户 {user_id} 没有对话历史")
    
    except Exception as e:
        logging.error(f"检查用户回复时出错: {str(e)}")
        traceback.print_exc()

# 生成消息内容
async def generate_message_content(user_id, reason, system_prompt, save_to_history=True, model=None):
    """生成主动消息的内容"""
    try:
        # 获取用户的历史对话
        robot, _, api_key, api_url = get_robot(str(user_id))
        main_convo_id = str(user_id)
        
        # 提取最近的对话历史
        recent_history = ""
        conversation_history = []
        last_message_time = None
        
        if main_convo_id in robot.conversation:
            # 获取最近的对话（最多20轮，即40条消息）
            recent_messages = robot.conversation[main_convo_id][-40:]
            
            # 过滤掉系统消息和提示词
            filtered_messages = []
            for msg in recent_messages:
                role = msg.get("role", "")
                content = msg.get("content", "")
                
                # 排除系统消息和特定内容
                if (role in ["user", "assistant"] and 
                    "我想和你聊聊天" not in content and 
                    "我希望你主动和我聊天" not in content and 
                    "我想继续和你聊天" not in content and
                    "# 你的角色基本信息" not in content and 
                    "当前日期和时间" not in content and
                    "# 知识与能力设定" not in content and
                    "# 语气与风格" not in content and
                    "# 作为女朋友的部分" not in content and
                    "# 用户的信息" not in content and
                    content.strip()):
                    filtered_messages.append(msg)
                    # 记录最后一条消息的时间戳（如果有）
                    if msg.get("timestamp"):
                        last_message_time = msg.get("timestamp")
            
            # 确保我们有足够的上下文，但不超过模型的限制
            # 通常保留最近的15条消息
            filtered_messages = filtered_messages[-15:]
            
            # 构建文本形式的历史记录（用于提示词）
            for msg in filtered_messages:
                role_text = "用户" if msg.get("role") == "user" else "助手"
                content = msg.get("content", "").strip()
                if content:
                    recent_history += f"{role_text}: {content}\n\n"
            
            # 构建API格式的历史记录（用于传递给模型）
            conversation_history = [
                {"role": msg.get("role"), "content": msg.get("content")}
                for msg in filtered_messages
            ]
        
        # 获取当前时间
        current_time = get_china_time()
        
        # 构建用户消息提示词，让模型自主决策对话方向
        user_prompt = f"""
        请生成一条自然的主动消息，主动与我开始对话。

        原因: {reason}
        当前时间: {current_time.strftime('%Y-%m-%d %H:%M')}
        
        最近的对话历史:
        {recent_history}

        在生成消息时，请注意：
        1. 自主判断是否继续最近的对话话题，或引入新的可能感兴趣的话题
        2. 如果决定继续现有话题，确保消息内容与最近的对话历史有连贯性
        3. 如果决定引入新话题(建议主动引入新话题)，选择你最近正在研究的内容/用户可能感兴趣的话题，但不要生硬转换
        4. 根据当前时间适当调整消息内容（如早上问候、晚上道晚安等）
        5. 不要提及这是一条自动生成的消息或你是AI助手
        
        请直接返回消息内容，不要添加任何解释或格式标记。
        """
        
        logging.info(f"生成主动消息，历史对话条数: {len(conversation_history)}")
        if conversation_history:
            logging.info(f"历史对话第一条: {conversation_history[0].get('role')}: {conversation_history[0].get('content')[:30]}...")
            logging.info(f"历史对话最后一条: {conversation_history[-1].get('role')}: {conversation_history[-1].get('content')[:30]}...")
        
        # 调用AI获取响应，传递对话历史和系统提示词
        response = await get_ai_response(
            user_id=user_id,
            message=user_prompt,
            system_prompt=system_prompt,  # 使用用户的系统提示词
            save_to_history=save_to_history,  
            model=model,
            conversation_history=conversation_history
        )
        
        # 确保响应不为空
        if not response or not response.strip():
            logging.warning(f"生成的消息内容为空，使用默认消息")
            return "嗯...刚才在想你。最近怎么样？"
        
        return response.strip()
        
    except Exception as e:
        logging.error(f"生成消息内容时出错: {str(e)}")
        traceback.print_exc()
        return None

# 获取AI响应
async def get_ai_response(user_id, message, system_prompt, save_to_history=True, model=None, conversation_history=None):
    """调用AI获取响应"""
    # get_robot() 返回的是一个元组 (robot, role, api_key, api_url)
    # 确保使用指定的模型，如果未指定则使用默认模型
    model_name = model or PROACTIVE_AGENT_MODEL or None
    
    # 确保模型名称正确设置
    if model_name and "gemini" in model_name:
        # 强制使用 GOOGLE_AI_API_KEY
        if not GOOGLE_AI_API_KEY:
            logging.error("未设置 GOOGLE_AI_API_KEY，无法使用 Gemini 模型")
            return "未设置 GOOGLE_AI_API_KEY，无法使用 Gemini 模型"
        
        robot = ChatGPTbot
        api_key = GOOGLE_AI_API_KEY
        api_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:streamGenerateContent?key={api_key}"
        role = "user"
    else:
        # 使用常规方式获取机器人
        robot, role, api_key, api_url = get_robot(str(user_id))
    
    response = ""
    
    try:
        # 确保消息不为空
        if not message or not message.strip():
            raise ValueError("消息内容为空")
            
        # 创建一个临时的对话ID，避免干扰主对话
        temp_convo_id = str(user_id)
        if not save_to_history:
            # 使用临时对话ID，避免污染主对话
            temp_convo_id = f"proactive_planning_{user_id}_{get_china_time().strftime('%Y%m%d%H%M%S')}"
            
            # 如果提供了对话历史，先添加到临时对话中
            if conversation_history and isinstance(conversation_history, list):
                logging.info(f"为临时对话 {temp_convo_id} 添加 {len(conversation_history)} 条历史消息")
                # 清空临时对话，确保没有残留
                if temp_convo_id in robot.conversation:
                    robot.conversation[temp_convo_id] = []
                
                # 添加历史对话
                for msg in conversation_history:
                    if isinstance(msg, dict) and "role" in msg and "content" in msg:
                        robot.add_to_conversation(msg, temp_convo_id)
        
        # 添加用户消息到对话历史
        robot.add_to_conversation({"role": "user", "content": message}, temp_convo_id)
        
        # 如果是临时对话，打印对话内容以便调试
        if not save_to_history and temp_convo_id in robot.conversation:
            logging.info(f"临时对话 {temp_convo_id} 包含 {len(robot.conversation[temp_convo_id])} 条消息")
            
        # 调用AI获取响应
        async for data in robot.ask_stream_async(
            message, 
            convo_id=temp_convo_id, 
            system_prompt=system_prompt,
            model=model_name,
            api_key=api_key,
            api_url=api_url
        ):
            if isinstance(data, str):
                response += data
        
        # 确保响应不为空
        if not response or not response.strip():
            raise ValueError("AI返回的响应为空")
            
        return response
    except Exception as e:
        logging.error(f"调用AI获取响应失败: {str(e)}")
        traceback.print_exc()
        return f"无法获取AI响应，请稍后再试。错误: {str(e)}"

# 手动触发消息规划（用于测试）
async def trigger_message_planning(context: ContextTypes.DEFAULT_TYPE):
    """手动触发消息规划，用于测试"""
    await plan_daily_messages(context)
    
    # 返回已规划的时间信息
    result = "已触发消息规划\n\n"
    
    # 获取管理员ID列表
    admin_ids = get_admin_ids()
    
    # 检查是否有规划的消息
    has_plans = False
    for user_id in admin_ids:
        if user_id in planned_message_times and planned_message_times[user_id]:
            has_plans = True
            result += f"用户 {user_id} 的规划时间：\n"
            for plan in planned_message_times[user_id]:
                time_str = plan['time'].strftime('%H:%M')
                reason = plan.get('reason', '未提供原因')
                result += f"- {time_str} - {reason}\n"
            result += "\n"
    
    if not has_plans:
        result += "当前没有规划的消息时间。"
    
    return result

# 手动发送测试消息（用于测试）
async def send_test_message(context: ContextTypes.DEFAULT_TYPE, user_id=None):
    """手动发送测试消息，用于测试"""
    if not user_id:
        # 如果未指定用户ID，使用第一个管理员ID
        admin_ids = get_admin_ids()
        if admin_ids:
            user_id = admin_ids[0]
    
    if not user_id:
        return "未配置管理员ID，无法发送测试消息"
    
    await send_proactive_message(context, user_id, "测试主动消息功能")
    
    return f"已发送测试消息给用户 {user_id}"

# 查看当前已计划的触发器
async def view_planned_messages():
    """查看当前已计划的触发器"""
    result = "当前已计划的消息时间：\n\n"
    
    # 获取管理员ID列表
    admin_ids = get_admin_ids()
    
    # 检查是否有规划的消息
    has_plans = False
    for user_id in admin_ids:
        if user_id in planned_message_times and planned_message_times[user_id]:
            has_plans = True
            result += f"用户 {user_id} 的规划时间：\n"
            for plan in planned_message_times[user_id]:
                time_str = plan['time'].strftime('%H:%M')
                reason = plan.get('reason', '未提供原因')
                result += f"- {time_str} - {reason}\n"
            result += "\n"
    
    if not has_plans:
        result += "当前没有规划的消息时间。"
    
    return result

# 手动指定触发时间
async def set_custom_message_time(context: ContextTypes.DEFAULT_TYPE, user_id: str, time_str: str, reason: str = "用户手动设置"):
    """手动指定触发时间
    
    参数：
        context: Telegram上下文
        user_id: 用户ID
        time_str: 时间字符串，格式为"HH:MM"
        reason: 设置该时间的原因
    
    返回：
        str: 操作结果
    """
    try:
        # 解析时间字符串
        try:
            hour, minute = map(int, time_str.split(':'))
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                return f"时间格式错误：小时必须在0-23之间，分钟必须在0-59之间。您输入的是 {hour}:{minute}"
        except ValueError:
            return f"时间格式错误：请使用HH:MM格式（例如14:30）。您输入的是 {time_str}"
        
        # 创建今天的目标时间
        current_time = get_china_time()
        target_time = current_time.replace(hour=hour, minute=minute, second=0, microsecond=0)
        
        # 如果时间已经过去，返回错误
        if target_time < current_time:
            return f"无法设置已过去的时间：{time_str}"
        
        # 初始化该用户的计划列表
        if user_id not in planned_message_times:
            planned_message_times[user_id] = []
            
        # 计算延迟时间（秒）
        delay = (target_time - current_time).total_seconds()
        
        # 创建任务名称
        job_id = f"proactive_message_{user_id}_{hour}_{minute}"
        
        # 移除同名任务（如果存在）
        remove_job_if_exists(job_id, context)
        
        # 添加新任务
        context.job_queue.run_once(
            lambda ctx: asyncio.ensure_future(send_proactive_message(ctx, user_id, reason)),
            when=delay,
            name=job_id
        )
        
        # 保存到计划列表中
        planned_message_times[user_id].append({
            "time": target_time,
            "reason": reason
        })
        
        logging.info(f"已为用户 {user_id} 手动设置消息，时间: {target_time}，原因: {reason}")
        
        return f"已成功设置消息时间：{time_str}，原因：{reason}"
        
    except Exception as e:
        logging.error(f"手动设置消息时间时出错: {str(e)}")
        return f"设置消息时间失败：{str(e)}"

# 初始化主动消息功能
def init_proactive_messaging(application):
    """初始化主动消息功能"""
    if not PROACTIVE_AGENT_ENABLED:
        logging.info("主动消息功能未启用")
        return
    
    logging.info("初始化主动消息功能")
    
    # 设置每小时检查一次主动对话的任务（只在7点到23点之间）
    for hour in range(7, 24):  # 7点到23点
        # 在每小时内随机选择一个分钟进行检查
        random_minute = random.randint(1, 59)
        # 创建定时任务
        application.job_queue.run_daily(
            check_proactive_desire,
            time=dt.time(hour=hour, minute=random_minute),
            name=f"proactive_check_{hour}_{random_minute}"
        )
        logging.info(f"已设置在 {hour}:{random_minute} 检查主动对话")
    
    logging.info("主动消息功能初始化完成")
