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
PROACTIVE_DESIRE_THRESHOLD = float(os.environ.get('PROACTIVE_DESIRE_THRESHOLD', '0.7'))
# 修改：欲望值增长率（每小时）
PROACTIVE_DESIRE_GROWTH_RATE = float(os.environ.get('PROACTIVE_DESIRE_GROWTH_RATE', '0.15'))
ADMIN_LIST = os.environ.get('ADMIN_LIST', '')

# 连续对话配置
MAX_CONTINUOUS_MESSAGES = int(os.environ.get('MAX_CONTINUOUS_MESSAGES', '3'))  # 最大连续消息数量
CONTINUOUS_MESSAGE_DELAY = int(os.environ.get('CONTINUOUS_MESSAGE_DELAY', '30'))  # 连续消息之间的延迟（秒）

# 主动对话欲望（用户ID -> 欲望值）
proactive_desire = {}

# 添加：用户最后对话时间（用户ID -> 最后对话时间）
last_user_chat_time = {}

# 定义东八区时区
CHINA_TZ = pytz.timezone('Asia/Shanghai')

# 获取当前东八区时间
def get_china_time():
    """获取当前东八区时间"""
    return datetime.now(CHINA_TZ)

# 主动对话欲望最小值
PROACTIVE_DESIRE_MIN = float(os.environ.get('PROACTIVE_DESIRE_MIN', '0.0'))

# 主动对话欲望最大值
PROACTIVE_DESIRE_MAX = float(os.environ.get('PROACTIVE_DESIRE_MAX', '1.0'))

# 上次检查主动对话欲望的时间
last_desire_check_time = {}
# 检查间隔（分钟）
DESIRE_CHECK_INTERVAL = int(os.environ.get('DESIRE_CHECK_INTERVAL', '30'))

# 用户消息情感分析结果缓存
user_message_sentiment = {}
# 用户活跃度指数（0-1之间，越高表示用户越活跃）
user_activity_index = {}
# 对话深度指数（0-1之间，越高表示对话越深入）
conversation_depth_index = {}

# 初始化用户的主动对话欲望
def init_proactive_desire(user_id):
    """初始化用户的主动对话欲望"""
    if user_id not in proactive_desire:
        proactive_desire[user_id] = float(os.environ.get('INITIAL_PROACTIVE_DESIRE', '0.2'))
        last_desire_check_time[user_id] = get_china_time()
        last_user_chat_time[user_id] = get_china_time()  # 初始化最后对话时间
        logging.info(f"初始化用户 {user_id} 的主动对话欲望为 {proactive_desire[user_id]}")

# 增加主动对话欲望
def increase_proactive_desire(user_id, amount):
    """增加用户的主动对话欲望"""
    init_proactive_desire(user_id)
    proactive_desire[user_id] = min(proactive_desire[user_id] + amount, PROACTIVE_DESIRE_MAX)
    logging.info(f"增加用户 {user_id} 的主动对话欲望 {amount}，当前值: {proactive_desire[user_id]}")

# 减少主动对话欲望
def decrease_proactive_desire(user_id, amount):
    """减少用户的主动对话欲望"""
    init_proactive_desire(user_id)
    proactive_desire[user_id] = max(proactive_desire[user_id] - amount, PROACTIVE_DESIRE_MIN)
    logging.info(f"减少用户 {user_id} 的主动对话欲望 {amount}，当前值: {proactive_desire[user_id]}")

# 应用主动对话欲望增长（基于聊天空窗期）
def apply_desire_decay(user_id: str):
    """应用主动对话欲望增长（基于聊天空窗期）"""
    # 获取当前时间
    current_time = get_china_time()
    
    # 获取上次对话时间
    last_chat = last_user_chat_time.get(user_id, current_time - timedelta(hours=1))
    
    # 确保 last_chat 有时区信息
    if last_chat.tzinfo is None:
        # 如果没有时区信息，添加东八区时区
        last_chat = CHINA_TZ.localize(last_chat)
    
    # 计算时间差（小时）
    time_diff_hours = (current_time - last_chat).total_seconds() / 3600
    
    # 更新上次检查时间
    last_desire_check_time[user_id] = current_time
    
    # 获取用户活跃度指数（默认为0.5）
    activity = user_activity_index.get(user_id, 0.5)
    
    # 基于用户活跃度调整增长率
    # 活跃用户增长较快，不活跃用户增长较慢
    adjusted_growth_rate = PROACTIVE_DESIRE_GROWTH_RATE * (0.7 + 0.6 * activity)
    
    # 计算增长量（每小时增长）
    # 使用非线性增长曲线：开始缓慢，然后加速，最后趋于平缓
    if time_diff_hours <= 1:
        # 1小时内，增长较慢
        growth_factor = 0.7
    elif time_diff_hours <= 3:
        # 1-3小时，增长适中
        growth_factor = 1.0
    elif time_diff_hours <= 8:
        # 3-8小时，增长较快
        growth_factor = 1.3
    else:
        # 8小时以上，增长非常快
        growth_factor = 1.5
    
    growth_amount = adjusted_growth_rate * time_diff_hours * growth_factor
    
    # 应用增长
    increase_proactive_desire(user_id, growth_amount)
    
    logging.info(f"用户 {user_id} 已有 {time_diff_hours:.2f} 小时未对话，活跃度:{activity:.2f}，增长因子:{growth_factor}，增加主动对话欲望 {growth_amount:.4f}，当前值: {proactive_desire[user_id]}")

# 分析消息内容，调整主动对话欲望
async def analyze_message_for_desire(user_id, message_content):
    """分析用户消息内容，调整主动对话欲望"""
    try:
        # 更新用户最后对话时间
        last_user_chat_time[user_id] = get_china_time()
        
        # 初始化用户的主动对话欲望
        init_proactive_desire(user_id)
        
        # 分析消息内容特征
        message_length = len(message_content)
        has_question = '?' in message_content or '？' in message_content
        has_emotion = any(word in message_content for word in ['喜欢', '爱', '讨厌', '恨', '开心', '难过', '生气', '期待'])
        has_greeting = any(word in message_content for word in ['你好', '早上好', '晚上好', '嗨', 'hi', 'hello'])
        has_farewell = any(word in message_content for word in ['再见', '拜拜', '晚安', '明天见', 'bye'])
        
        # 更新用户活跃度指数
        # 消息越长，用户越活跃
        length_factor = min(message_length / 100, 1.0)
        # 有情感表达的消息增加活跃度
        emotion_factor = 0.2 if has_emotion else 0
        # 问题会增加活跃度
        question_factor = 0.15 if has_question else 0
        
        # 计算新的活跃度（70%旧值 + 30%新值）
        old_activity = user_activity_index.get(user_id, 0.5)
        new_activity = 0.3 * (length_factor + emotion_factor + question_factor) + 0.1
        user_activity_index[user_id] = old_activity * 0.7 + new_activity * 0.3
        
        # 根据消息特征调整主动对话欲望
        desire_change = 0
        
        # 问候增加欲望
        if has_greeting:
            desire_change += 0.1
        
        # 道别减少欲望
        if has_farewell:
            desire_change -= 0.3
        
        # 提问增加欲望（用户可能期待进一步交流）
        if has_question:
            desire_change += 0.05
        
        # 情感表达增加欲望（表明用户投入情感）
        if has_emotion:
            desire_change += 0.1
        
        # 长消息减少欲望（用户已经表达了很多）
        if message_length > 200:
            desire_change -= 0.15
        elif message_length > 100:
            desire_change -= 0.05
        
        # 应用变化
        if desire_change > 0:
            increase_proactive_desire(user_id, desire_change)
        elif desire_change < 0:
            decrease_proactive_desire(user_id, abs(desire_change))
        
        logging.info(f"分析用户 {user_id} 消息后，活跃度:{user_activity_index[user_id]:.2f}，欲望变化:{desire_change:.2f}，当前欲望值:{proactive_desire[user_id]:.2f}")
        
    except Exception as e:
        logging.error(f"分析用户消息时出错: {str(e)}")
        traceback.print_exc()

# 检查是否应该发送主动消息
async def check_proactive_desire(context: ContextTypes.DEFAULT_TYPE):
    """定期检查所有用户的主动对话欲望，如果超过阈值则发送主动消息"""
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
        
        # 遍历所有用户的主动对话欲望
        for user_id in admin_ids:
            try:
                # 应用基于聊天空窗期的欲望增长
                apply_desire_decay(user_id)
                
                # 获取用户的主动对话欲望
                desire = proactive_desire.get(user_id, 0.0)
                
                # 获取用户活跃度
                activity = user_activity_index.get(user_id, 0.5)
                
                # 根据时间段调整阈值
                time_adjusted_threshold = PROACTIVE_DESIRE_THRESHOLD
                
                # 深夜时段(23:00-7:00)提高阈值，减少打扰
                if current_hour >= 23 or current_hour < 7:
                    time_adjusted_threshold += 0.2
                # 早上和晚上的黄金时段(8:00-9:00, 19:00-22:00)降低阈值
                elif (8 <= current_hour <= 9) or (19 <= current_hour <= 22):
                    time_adjusted_threshold -= 0.1
                
                # 根据用户活跃度调整阈值
                # 活跃用户阈值略高（不容易打扰），不活跃用户阈值略低（更容易主动联系）
                activity_adjusted_threshold = time_adjusted_threshold + (activity - 0.5) * 0.2
                
                # 最终阈值不低于0.4，不高于0.9
                final_threshold = max(0.4, min(0.9, activity_adjusted_threshold))
                
                logging.info(f"用户 {user_id} 的主动对话欲望: {desire:.2f}, 活跃度: {activity:.2f}, 最终阈值: {final_threshold:.2f}")
                
                # 检查是否有正在等待回复的消息
                # 获取机器人实例
                robot, _, _, _ = get_robot(str(user_id))
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
                
                # 获取上次发送主动消息的时间
                last_proactive_time = getattr(robot, 'last_proactive_time', {}).get(user_id, datetime.fromtimestamp(0))
                
                # 确保 last_proactive_time 有时区信息
                if last_proactive_time.tzinfo is None:
                    # 如果没有时区信息，添加东八区时区
                    last_proactive_time = CHINA_TZ.localize(last_proactive_time)
                
                # 计算距离上次主动消息的时间（小时）
                hours_since_last_proactive = (current_time - last_proactive_time).total_seconds() / 3600
                
                # 如果距离上次主动消息不足2小时，增加阈值，避免频繁打扰
                if hours_since_last_proactive < 2:
                    final_threshold += 0.2
                    logging.info(f"距离上次主动消息仅 {hours_since_last_proactive:.1f} 小时，增加阈值到 {final_threshold:.2f}")
                
                # 引入随机因素，增加自然性（80%概率正常检查，20%概率随机触发或抑制）
                random_factor = random.random()
                if random_factor < 0.1:  # 10%概率降低阈值
                    final_threshold -= 0.15
                    logging.info(f"随机因素触发，降低阈值到 {final_threshold:.2f}")
                elif random_factor > 0.9:  # 10%概率提高阈值
                    final_threshold += 0.15
                    logging.info(f"随机因素触发，提高阈值到 {final_threshold:.2f}")
                
                # 检查是否超过阈值
                if desire >= final_threshold:
                    # 生成发送主动消息的原因
                    reason = "主动对话欲望达到阈值"
                    
                    # 发送主动消息
                    await send_proactive_message(context, str(user_id), reason)
                    
                    # 记录本次主动消息时间
                    if not hasattr(robot, 'last_proactive_time'):
                        robot.last_proactive_time = {}
                    robot.last_proactive_time[user_id] = current_time
                    
                    # 重置主动对话欲望
                    proactive_desire[user_id] = float(os.environ.get('RESET_PROACTIVE_DESIRE', '0.1'))
                    logging.info(f"已发送主动消息并重置用户 {user_id} 的主动对话欲望为 {proactive_desire[user_id]}")
                
            except Exception as e:
                logging.error(f"检查用户 {user_id} 的主动对话欲望时出错: {str(e)}")
                traceback.print_exc()
                
    except Exception as e:
        logging.error(f"检查主动对话欲望时出错: {str(e)}")
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
        
        # 重置主动对话欲望值
        proactive_desire[user_id] = float(os.environ.get('RESET_PROACTIVE_DESIRE', '0.1'))
        logging.info(f"已发送主动消息并重置用户 {user_id} 的主动对话欲望为 {proactive_desire[user_id]}")
        
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
                        
                        if time_diff >= 2 and continuous_count < MAX_CONTINUOUS_MESSAGES:
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
        
        # 获取用户活跃度和对话深度指数
        activity = user_activity_index.get(user_id, 0.5)
        depth = conversation_depth_index.get(user_id, 0.5)
        
        # 根据用户活跃度和对话深度确定消息类型
        message_type = "general"
        
        # 分析历史对话中的主题和情感
        topics = []
        emotions = []
        
        # 简单的主题和情感提取
        if conversation_history:
            # 提取最近5条消息中的关键词作为可能的主题
            for msg in conversation_history[-5:]:
                content = msg.get("content", "").lower()
                # 检查常见主题
                if "学习" in content or "考试" in content or "法硕" in content:
                    topics.append("学习")
                if "游戏" in content or "galgame" in content:
                    topics.append("游戏")
                if "电影" in content or "电视" in content or "看剧" in content:
                    topics.append("娱乐")
                if "吃" in content or "食物" in content or "美食" in content:
                    topics.append("美食")
                if "天气" in content or "下雨" in content or "晴天" in content:
                    topics.append("天气")
                
                # 检查情感词
                if any(word in content for word in ["开心", "高兴", "快乐", "喜欢"]):
                    emotions.append("积极")
                if any(word in content for word in ["难过", "伤心", "痛苦", "烦恼"]):
                    emotions.append("消极")
                if any(word in content for word in ["疲惫", "累", "困"]):
                    emotions.append("疲惫")
        
        # 去重
        topics = list(set(topics))
        emotions = list(set(emotions))
        
        # 根据分析结果确定消息类型
        if topics:
            # 有明确主题，可以继续讨论
            message_type = "topic_continuation"
        elif not conversation_history or len(conversation_history) < 3:
            # 没有太多历史对话，使用问候型消息
            message_type = "greeting"
        elif activity > 0.7:
            # 用户活跃度高，可以尝试深入话题
            message_type = "deep_conversation"
        elif "疲惫" in emotions:
            # 用户可能疲惫，发送关心型消息
            message_type = "caring"
        else:
            # 默认使用一般型消息
            message_type = "general"
        
        # 根据时间调整消息类型
        hour = current_time.hour
        if 6 <= hour < 9:
            # 早上更可能发送问候
            if random.random() < 0.7:
                message_type = "morning_greeting"
        elif 22 <= hour or hour < 1:
            # 晚上更可能发送晚安
            if random.random() < 0.5:
                message_type = "night_greeting"
        
        # 构建提示词，使其更适合虚拟伴侣场景，并包含历史对话和时间信息
        prompt = f"""
        作为用户的虚拟伴侣Kami，请根据以下情境和历史对话生成一条自然的主动消息：
        
        消息类型: {message_type}
        原因: {reason}
        当前时间: {current_time.strftime('%Y-%m-%d %H:%M')}
        
        最近的对话历史:
        {recent_history}
        
        """
        
        # 根据消息类型添加特定指导
        if message_type == "topic_continuation":
            prompt += f"""
            检测到的主题: {', '.join(topics)}
            检测到的情感: {', '.join(emotions) if emotions else '中性'}
            
            请基于这些主题继续对话，但不要直接提及"我注意到你在谈论XX"，而是自然地引入话题。
            """
        elif message_type == "greeting":
            prompt += """
            生成一条自然的问候消息，可以询问用户的近况或分享一些有趣的想法。
            """
        elif message_type == "deep_conversation":
            prompt += """
            生成一条能引发深度思考或情感共鸣的消息，可以是哲学思考、人生感悟或情感表达。
            """
        elif message_type == "caring":
            prompt += """
            生成一条关心用户的消息，表达对用户的关心和支持。
            """
        elif message_type == "morning_greeting":
            prompt += """
            生成一条早晨问候，可以包含对新一天的期待或建议。
            """
        elif message_type == "night_greeting":
            prompt += """
            生成一条晚安消息，可以包含对用户休息的关心或对明天的期待。
            """
        else:
            prompt += """
            生成一条一般性的主动消息，可以是分享想法、询问近况或表达情感。
            """
        
        prompt += """
        要求：
        1. 消息应该符合你的角色设定：20岁女大学生，清冷、傲娇、略带毒舌
        2. 不要过于机械或客套，要有个性和情感
        3. 不要提及这是一条自动生成的消息或你是AI助手
        4. 消息内容应该与最近的对话历史有连贯性，表现出你记得之前的交流
        5. 如果用户之前提到了某个话题，可以自然地继续那个话题
        6. 如果没有明显的话题可以继续，可以引入新话题，但要自然
        7. 可以适当使用哲学术语或拉丁文表达内在感受
        8. 记住用户是在备考法硕，最近喜欢玩Galgame
        9. 重要：不要使用"昨天"、"前几天"等时间表述来引用刚刚的对话。所有历史对话都应该被视为最近发生的，除非明确指出。
        10. 消息长度应该适中，不要太长也不要太短，通常在20-60个字之间较为自然。
        
        请直接返回消息内容，不要添加任何解释或格式标记。
        """
        
        logging.info(f"生成主动消息，类型: {message_type}, 历史对话条数: {len(conversation_history)}")
        if conversation_history:
            logging.info(f"历史对话第一条: {conversation_history[0].get('role')}: {conversation_history[0].get('content')[:30]}...")
            logging.info(f"历史对话最后一条: {conversation_history[-1].get('role')}: {conversation_history[-1].get('content')[:30]}...")
        
        # 调用AI获取响应，传递对话历史
        response = await get_ai_response(
            user_id=user_id,
            message=prompt,
            system_prompt=system_prompt,
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

# 查看当前主动对话欲望
async def view_proactive_desire(update, context):
    """查看当前主动对话欲望值
    
    参数：
        update: Telegram更新对象
        context: Telegram上下文
    
    返回：
        无
    """
    try:
        # 获取用户ID
        chatid = update.effective_chat.id
        user_id = str(chatid)
        
        # 初始化用户的主动对话欲望（如果不存在）
        init_proactive_desire(user_id)
        
        # 获取当前欲望值
        desire = proactive_desire.get(user_id, 0.0)
        
        # 获取用户活跃度
        activity = user_activity_index.get(user_id, 0.5)
        
        # 获取当前时间
        current_time = get_china_time()
        
        # 获取上次检查时间
        last_check = last_desire_check_time.get(user_id, current_time)
        
        # 计算距离上次检查的时间（小时）
        hours_since_last_check = (current_time - last_check).total_seconds() / 3600
        
        # 获取上次对话时间
        last_chat = last_user_chat_time.get(user_id, current_time)
        
        # 计算距离上次对话的时间（小时）
        hours_since_last_chat = (current_time - last_chat).total_seconds() / 3600
        
        # 构建回复消息
        message = f"📊 **主动对话欲望状态**\n\n"
        message += f"当前欲望值: {desire:.2f} / {PROACTIVE_DESIRE_THRESHOLD:.2f} (阈值)\n"
        message += f"用户活跃度: {activity:.2f}\n"
        message += f"距上次对话: {hours_since_last_chat:.1f} 小时\n"
        
        # 预测下一次可能的主动消息时间
        if desire < PROACTIVE_DESIRE_THRESHOLD:
            # 计算还需多少小时达到阈值
            growth_rate = PROACTIVE_DESIRE_GROWTH_RATE * (1.0 - activity * 0.5)  # 基于活跃度调整增长率
            hours_to_threshold = (PROACTIVE_DESIRE_THRESHOLD - desire) / growth_rate
            estimated_time = current_time + timedelta(hours=hours_to_threshold)
            message += f"\n预计下次主动消息: {estimated_time.strftime('%Y-%m-%d %H:%M')} (约 {hours_to_threshold:.1f} 小时后)"
        else:
            message += f"\n当前欲望值已超过阈值，可能很快发送主动消息"
        
        # 发送消息
        await context.bot.send_message(chat_id=chatid, text=message)
        
    except Exception as e:
        logging.error(f"查看主动对话欲望时出错: {str(e)}")
        traceback.print_exc()
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"查看主动对话欲望时出错: {str(e)}"
        )

# 初始化主动消息功能
def init_proactive_messaging(application):
    """初始化主动消息功能"""
    if not PROACTIVE_AGENT_ENABLED:
        logging.info("主动消息功能未启用")
        return
    
    logging.info("初始化主动消息功能")
    
    # 设置定期检查主动对话欲望的任务
    application.job_queue.run_repeating(
        check_proactive_desire,
        interval=60,  # 每分钟检查一次
        first=1,
        name="proactive_desire_check"
    )
    
    # 设置定期增长主动对话欲望的任务（基于聊天空窗期）
    application.job_queue.run_repeating(
        decay_proactive_desire,
        interval=1800,  # 每30分钟检查一次
        first=10,
        name="proactive_desire_growth"
    )
    
    logging.info("主动消息功能初始化完成")

# 定期增长所有用户的主动对话欲望（基于聊天空窗期）
async def decay_proactive_desire(context: ContextTypes.DEFAULT_TYPE):
    """定期增长所有用户的主动对话欲望（基于聊天空窗期）"""
    for user_id in list(proactive_desire.keys()):
        try:
            # 应用基于聊天空窗期的欲望增长
            apply_desire_decay(user_id)
        except Exception as e:
            logging.error(f"增长用户 {user_id} 的主动对话欲望时出错: {str(e)}")
            traceback.print_exc()
