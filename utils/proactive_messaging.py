import os
import json
import logging
import random
from datetime import datetime, timedelta
import asyncio
import re
import datetime as dt
import pytz  # 添加pytz库用于时区转换

from telegram.ext import ContextTypes
from config import Users, get_robot, GOOGLE_AI_API_KEY, ChatGPTbot

# 配置项
PROACTIVE_AGENT_ENABLED = os.environ.get('PROACTIVE_AGENT_ENABLED', 'false').lower() == 'true'
PROACTIVE_AGENT_MODEL = os.environ.get('PROACTIVE_AGENT_MODEL', 'gemini-2.5-flash-preview-04-17')
PROACTIVE_DESIRE_THRESHOLD = float(os.environ.get('PROACTIVE_DESIRE_THRESHOLD', '0.7'))
PROACTIVE_DESIRE_DECAY_RATE = float(os.environ.get('PROACTIVE_DESIRE_DECAY_RATE', '0.01'))
ADMIN_LIST = os.environ.get('ADMIN_LIST', '')

# 连续对话配置
MAX_CONTINUOUS_MESSAGES = int(os.environ.get('MAX_CONTINUOUS_MESSAGES', '3'))  # 最大连续消息数量
CONTINUOUS_MESSAGE_DELAY = int(os.environ.get('CONTINUOUS_MESSAGE_DELAY', '30'))  # 连续消息之间的延迟（秒）

# 主动对话欲望值（用户ID -> 欲望值）
proactive_desire = {}

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

# 初始化用户的主动对话欲望
def init_proactive_desire(user_id):
    """初始化用户的主动对话欲望"""
    if user_id not in proactive_desire:
        proactive_desire[user_id] = float(os.environ.get('INITIAL_PROACTIVE_DESIRE', '0.2'))
        last_desire_check_time[user_id] = get_china_time()
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

# 应用主动对话欲望衰减
def apply_desire_decay(user_id: str):
    """应用主动对话欲望衰减"""
    # 获取上次检查时间
    last_check = last_desire_check_time.get(user_id, datetime.now(CHINA_TZ) - timedelta(minutes=DESIRE_CHECK_INTERVAL))
    
    # 计算时间差（分钟）
    time_diff = (datetime.now(CHINA_TZ) - last_check).total_seconds() / 60
    
    # 如果时间差小于检查间隔，跳过
    if time_diff < DESIRE_CHECK_INTERVAL:
        return
    
    # 更新上次检查时间
    last_desire_check_time[user_id] = datetime.now(CHINA_TZ)
    
    # 计算衰减量（每分钟衰减）
    decay_amount = PROACTIVE_DESIRE_DECAY_RATE * time_diff / (24 * 60)  # 按比例计算
    
    # 应用衰减
    decrease_proactive_desire(user_id, decay_amount)

# 分析消息内容，调整主动对话欲望
async def analyze_message_for_desire(user_id, message_content):
    """分析消息内容，调整主动对话欲望"""
    try:
        # 构建提示词
        prompt = f"""
        分析以下用户消息，评估我是否应该增加或减少与用户主动对话的欲望。
        
        用户消息: "{message_content}"
        
        请根据以下标准评估:
        1. 如果用户表达了希望继续交流的兴趣，应增加主动对话欲望
        2. 如果用户表达了不想被打扰的意愿，应减少主动对话欲望
        3. 如果用户提出了问题或表达了好奇心，应增加主动对话欲望
        4. 如果用户回应简短或敷衍，应减少主动对话欲望
        5. 如果用户分享了个人经历或情感，应增加主动对话欲望
        
        请仅返回一个JSON格式的结果:
        {{
            "adjustment": 浮点数(-0.2到0.2之间),
            "reason": "调整原因的简短解释"
        }}
        
        正数表示增加主动对话欲望，负数表示减少主动对话欲望。
        """
        
        # 调用AI分析，强制使用Gemini模型
        gemini_model = "gemini-2.5-pro-preview-03-25"  # 使用Gemini模型
        response = await get_ai_response(
            user_id=user_id,
            message=prompt,
            system_prompt="你是一个分析用户意图和情感的助手，你的任务是判断是否应该增加或减少与用户的主动交流频率。",
            save_to_history=False,
            model=gemini_model
        )
        
        # 解析响应
        try:
            # 提取JSON部分
            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if json_match:
                json_str = json_match.group(0)
                result = json.loads(json_str)
                
                adjustment = float(result.get("adjustment", 0))
                # 限制调整范围
                adjustment = max(min(adjustment, 0.2), -0.2)
                
                if adjustment > 0:
                    increase_proactive_desire(user_id, adjustment)
                elif adjustment < 0:
                    decrease_proactive_desire(user_id, abs(adjustment))
                
                logging.info(f"分析用户 {user_id} 消息后调整主动对话欲望: {adjustment}, 原因: {result.get('reason', '未提供')}")
                return adjustment
            else:
                logging.warning(f"无法从AI响应中提取JSON: {response}")
                return 0
        except Exception as e:
            logging.error(f"解析AI响应时出错: {str(e)}, 响应: {response}")
            return 0
            
    except Exception as e:
        logging.error(f"分析消息调整主动对话欲望时出错: {str(e)}")
        return 0

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
        
        # 遍历所有用户的主动对话欲望
        for user_id in admin_ids:
            try:
                # 应用欲望衰减
                apply_desire_decay(user_id)
                
                # 获取用户的主动对话欲望
                desire = proactive_desire.get(user_id, 0.0)
                logging.info(f"用户 {user_id} 的主动对话欲望: {desire}, 阈值: {PROACTIVE_DESIRE_THRESHOLD}")
                
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
                
                # 检查是否超过阈值
                if desire >= PROACTIVE_DESIRE_THRESHOLD:
                    # 生成发送主动消息的原因
                    reason = "主动对话欲望达到阈值"
                    
                    # 发送主动消息
                    await send_proactive_message(context, str(user_id), reason)
                    
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
        
        # 发送消息
        await context.bot.send_message(chat_id=user_id, text=message_content)
        
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
            # 获取最近的消息
            recent_messages = robot.conversation[main_convo_id][-5:]  # 只看最近5条
            
            for msg in recent_messages:
                if msg.get("role") == "user" and "我想和你聊聊天" not in msg.get("content", ""):
                    last_user_message_time = msg.get("timestamp", datetime.now(CHINA_TZ) - timedelta(minutes=10))
                elif msg.get("role") == "assistant":
                    last_bot_message_time = msg.get("timestamp", datetime.now(CHINA_TZ))
                    last_bot_message = msg.get("content", "")
            
            # 如果没有找到时间戳，使用默认值
            if not last_user_message_time:
                last_user_message_time = datetime.now(CHINA_TZ) - timedelta(minutes=10)
            if not last_bot_message_time:
                last_bot_message_time = datetime.now(CHINA_TZ) - timedelta(minutes=5)
            
            # 检查用户是否已回复（如果用户最后一条消息时间晚于机器人最后一条消息时间）
            if last_user_message_time and last_bot_message_time and last_user_message_time > last_bot_message_time:
                logging.info(f"用户 {user_id} 已回复，不需要发送连续消息")
                return
            
            # 检查是否已经发送了最大数量的连续消息
            continuous_count = 0
            for i in range(len(recent_messages) - 1, -1, -1):
                msg = recent_messages[i]
                if msg.get("role") == "user" and "我想和你聊聊天" in msg.get("content", ""):
                    continuous_count += 1
                elif msg.get("role") == "user" and "我想和你聊聊天" not in msg.get("content", ""):
                    # 遇到真实用户消息，停止计数
                    break
            
            if continuous_count >= MAX_CONTINUOUS_MESSAGES:
                logging.info(f"已达到最大连续消息数量 {MAX_CONTINUOUS_MESSAGES}，停止发送")
                return
            
            # 提取最近的对话历史
            recent_history = ""
            conversation_history = []
            last_message_time = None
            
            if main_convo_id in robot.conversation:
                # 获取最近的对话（最多10轮，即20条消息）
                recent_messages = robot.conversation[main_convo_id][-20:]
                
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
            # 通常保留最近的10条消息
            filtered_messages = filtered_messages[-10:]
            
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
        
        # 构建提示词，使其更适合虚拟伴侣场景，并包含历史对话和时间信息
        prompt = f"""
        作为用户的虚拟伴侣Kami，我刚刚发送了以下消息给用户，但用户还没有回复：
        "{last_bot_message}"
        
        当前时间：{get_china_time().strftime('%Y-%m-%d %H:%M')}
        
        最近的对话历史：
        {recent_history}
        
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
        
        请返回JSON格式：
        {{
            "should_continue": true/false,
            "reason": "决定原因",
            "message": "如果应该继续，这里是后续消息内容"
        }}
        """
        
        # 获取系统提示词
        system_prompt = Users.get_config(str(user_id), "systemprompt")
        
        # 添加当前东八区日期和时间
        current_datetime = datetime.now(CHINA_TZ)
        current_date = current_datetime.strftime("%Y-%m-%d")
        current_time = current_datetime.strftime("%H:%M")
        system_prompt = f"当前日期和时间（东八区）：{current_date} {current_time}\n\n{system_prompt}"
        
        # 获取AI回复
        model = os.environ.get('PROACTIVE_AGENT_MODEL', 'gemini-2.5-flash-preview-04-17')
        response = await get_ai_response(
            user_id=user_id,
            message=prompt,
            system_prompt=system_prompt,
            save_to_history=False,
            model=model
        )
        
        # 解析JSON响应
        try:
            # 尝试处理可能的Markdown格式
            json_str = response
            # 移除可能的Markdown代码块格式
            if "```json" in json_str:
                json_str = json_str.split("```json")[1].split("```")[0].strip()
            elif "```" in json_str:
                json_str = json_str.split("```")[1].split("```")[0].strip()
            
            result = json.loads(json_str)
            should_continue = result.get("should_continue", False)
            reason = result.get("reason", "")
            message = result.get("message", "")
            
            if should_continue and message:
                # 发送后续消息
                await context.bot.send_message(chat_id=user_id, text=message)
                
                # 将消息保存到对话历史
                if main_convo_id in robot.conversation:
                    # 添加虚拟的用户消息，表示这是连续对话
                    robot.add_to_conversation({"role": "user", "content": "我想继续和你聊天"}, main_convo_id)
                    # 添加机器人的回复，并包含时间戳
                    robot.add_to_conversation({
                        "role": "assistant", 
                        "content": message,
                        "timestamp": str(datetime.now(CHINA_TZ).timestamp())
                    }, main_convo_id)
                    
                    # 安排下一次检查
                    context.job_queue.run_once(
                        lambda ctx: asyncio.create_task(check_user_response(ctx, user_id)),
                        CONTINUOUS_MESSAGE_DELAY,
                        name=f"check_response_{user_id}"
                    )
                    
                    logging.info(f"已发送连续消息给用户 {user_id}，原因: {reason}")
            else:
                logging.info(f"决定不发送连续消息给用户 {user_id}，原因: {reason}")
        except json.JSONDecodeError:
            logging.error(f"无法解析AI响应为JSON: {response}")
        
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
            # 获取最近的对话（最多10轮，即20条消息）
            recent_messages = robot.conversation[main_convo_id][-20:]
            
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
            # 通常保留最近的10条消息
            filtered_messages = filtered_messages[-10:]
            
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
        
        # 构建提示词，使其更适合虚拟伴侣场景，并包含历史对话和时间信息
        prompt = f"""
        作为用户的虚拟伴侣Kami，请根据以下情境和历史对话生成一条自然的主动消息：
        
        原因：{reason}
        
        当前时间：{current_time.strftime('%Y-%m-%d %H:%M')}
        
        最近的对话历史：
        {recent_history}
        
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
        
        请直接返回消息内容，不要添加任何解释或格式标记。
        """
        
        logging.info(f"生成主动消息，历史对话条数: {len(conversation_history)}")
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
            logging.warning(f"生成的消息内容为空，将使用默认消息")
            return f"嗨，我在想你，所以来找你聊聊天~ {reason}"
            
        return response.strip()
    except Exception as e:
        logging.error(f"生成消息内容失败: {str(e)}")
        traceback.print_exc()
        return f"嗨，我在想你，所以来找你聊聊天~ {reason}"

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
    
    # 设置定期检查主动对话欲望的任务
    application.job_queue.run_repeating(
        check_proactive_desire,
        interval=60,  # 每分钟检查一次
        first=1,
        name="proactive_desire_check"
    )
    
    # 设置定期衰减主动对话欲望的任务
    application.job_queue.run_repeating(
        decay_proactive_desire,
        interval=3600,  # 每小时衰减一次
        first=10,
        name="proactive_desire_decay"
    )
    
    logging.info("主动消息功能初始化完成")

# 定期衰减所有用户的主动对话欲望
async def decay_proactive_desire(context: ContextTypes.DEFAULT_TYPE):
    """定期衰减所有用户的主动对话欲望"""
    for user_id in list(proactive_desire.keys()):
        try:
            # 计算衰减量
            decay_amount = proactive_desire[user_id] * PROACTIVE_DESIRE_DECAY_RATE
            # 应用衰减
            decrease_proactive_desire(user_id, decay_amount)
            logging.info(f"已衰减用户 {user_id} 的主动对话欲望，当前值: {proactive_desire[user_id]}")
        except Exception as e:
            logging.error(f"衰减用户 {user_id} 的主动对话欲望时出错: {str(e)}")
