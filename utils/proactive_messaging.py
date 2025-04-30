import os
import json
import logging
import random
from datetime import datetime, timedelta
import asyncio
import re

from telegram.ext import ContextTypes
from config import Users, get_robot

# 配置项
PROACTIVE_AGENT_ENABLED = os.environ.get('PROACTIVE_AGENT_ENABLED', 'False') == 'True'
ADMIN_LIST = os.environ.get('ADMIN_LIST', '')
PROACTIVE_AGENT_SYSTEM_PROMPT = os.environ.get('PROACTIVE_AGENT_SYSTEM_PROMPT', 
"""你是一个主动沟通的助手。你的任务是：
1. 每天决定2-3个适合的时间点，在这些时间点主动与用户沟通
2. 根据用户的历史对话和兴趣，生成有价值、有趣的消息
3. 避免在不适当的时间（如深夜）打扰用户
4. 你的消息应该有目的性，可以是：分享知识、提醒事项、询问进展、推荐内容等
请记住，你的目标是增强用户体验，而不是打扰用户。
""")
PROACTIVE_AGENT_MODEL = os.environ.get('PROACTIVE_AGENT_MODEL', '')  # 指定主动消息使用的模型

# 存储计划的消息时间
planned_message_times = {}

# 获取管理员列表
def get_admin_ids():
    """获取管理员ID列表"""
    if not ADMIN_LIST:
        return []
    
    admin_ids = []
    for admin_id in ADMIN_LIST.split(','):
        admin_id = admin_id.strip()
        if admin_id:
            admin_ids.append(admin_id)
    
    return admin_ids

# 获取AI响应
async def get_ai_response(user_id, message, system_prompt, save_to_history=True, model=None):
    """调用AI获取响应"""
    robot = get_robot()
    response = ""
    
    # 使用指定的模型，如果未指定则使用默认模型
    model_name = model or PROACTIVE_AGENT_MODEL or None
    
    async for data in robot.ask_stream_async(
        message, 
        convo_id=str(user_id), 
        system_prompt=system_prompt,
        model=model_name
    ):
        if isinstance(data, str):
            response += data
    
    return response

# 移除指定的任务
def remove_job_if_exists(name, context):
    """如果存在，则移除指定名称的任务"""
    current_jobs = context.job_queue.get_jobs_by_name(name)
    if not current_jobs:
        return False
    for job in current_jobs:
        job.schedule_removal()
    return True

# 移除所有计划的消息
def remove_all_planned_messages(context, user_id):
    """移除用户的所有计划消息"""
    if user_id in planned_message_times:
        for plan in planned_message_times[user_id]:
            job_name = f"proactive_message_{user_id}_{plan['time'].hour}_{plan['time'].minute}"
            remove_job_if_exists(job_name, context)
        planned_message_times[user_id] = []

# 每天凌晨规划当天的消息时间
async def plan_daily_messages(context: ContextTypes.DEFAULT_TYPE):
    """规划当天的主动消息时间"""
    if not PROACTIVE_AGENT_ENABLED:
        return
    
    # 获取管理员ID列表
    admin_ids = get_admin_ids()
    if not admin_ids:
        logging.warning("未配置管理员ID，无法发送主动消息")
        return
    
    # 为每个管理员规划消息
    for user_id in admin_ids:
        # 构建提示词，让AI决定今天的消息时间
        current_date = datetime.now().strftime('%Y-%m-%d')
        planning_prompt = f"""
        基于当前日期（{current_date}），请决定今天应该在哪2-3个时间点发送消息。
        考虑用户可能的作息时间，避免在不适当的时间（如深夜）打扰用户。
        
        你必须严格按照以下JSON格式返回，不要添加任何其他解释或文字：
        {{
            "message_times": [
                {{"hour": 小时(整数), "minute": 分钟(整数), "reason": "选择这个时间的原因"}},
                {{"hour": 小时(整数), "minute": 分钟(整数), "reason": "选择这个时间的原因"}},
                {{"hour": 小时(整数), "minute": 分钟(整数), "reason": "选择这个时间的原因"}}
            ]
        }}
        
        注意：
        1. hour必须是0-23之间的整数
        2. minute必须是0-59之间的整数
        3. 不要在JSON前后添加任何其他文本、代码块标记或解释
        4. 确保JSON格式完全正确，可以被解析
        """
        
        try:
            # 调用AI获取计划
            response = await get_ai_response(
                user_id=user_id, 
                message=planning_prompt, 
                system_prompt=PROACTIVE_AGENT_SYSTEM_PROMPT,
                save_to_history=False,  # 不保存这个规划过程到用户的对话历史
                model=PROACTIVE_AGENT_MODEL  # 使用指定的模型
            )
            
            # 尝试多种方式提取和解析JSON
            message_times = extract_time_from_response(response)
            
            if not message_times:
                logging.warning(f"无法从响应中提取有效的时间信息: {response}")
                continue
            
            # 清除之前的计划
            remove_all_planned_messages(context, user_id)
            
            # 安排新的消息时间
            scheduled_count = 0
            for time_slot in message_times:
                try:
                    hour = int(time_slot.get("hour", 12))
                    minute = int(time_slot.get("minute", 0))
                    reason = str(time_slot.get("reason", ""))
                    
                    # 验证时间值的有效性
                    if not (0 <= hour <= 23 and 0 <= minute <= 59):
                        logging.warning(f"无效的时间值: hour={hour}, minute={minute}")
                        continue
                    
                    # 创建今天的时间对象
                    now = datetime.now()
                    message_time = datetime(now.year, now.month, now.day, hour, minute)
                    
                    # 如果时间已过，跳过这个时间点
                    if message_time < now:
                        continue
                    
                    # 安排消息发送任务
                    job = context.job_queue.run_once(
                        send_proactive_message,
                        message_time,
                        data={"user_id": user_id, "reason": reason},
                        name=f"proactive_message_{user_id}_{hour}_{minute}"
                    )
                    
                    # 记录计划的消息时间
                    if user_id not in planned_message_times:
                        planned_message_times[user_id] = []
                    planned_message_times[user_id].append({
                        "time": message_time,
                        "job_id": job.id if hasattr(job, 'id') else str(random.randint(1000, 9999)),
                        "reason": reason
                    })
                    
                    scheduled_count += 1
                except (ValueError, TypeError) as e:
                    logging.error(f"处理时间槽时出错: {e}, 时间槽数据: {time_slot}")
            
            # 记录日志
            logging.info(f"计划了 {scheduled_count} 条主动消息给用户 {user_id}")
            
        except Exception as e:
            logging.error(f"为用户 {user_id} 规划消息时出错: {str(e)}")
            logging.error(f"AI响应: {response}")

def extract_time_from_response(response):
    """从AI响应中提取时间信息，尝试多种方法"""
    message_times = []
    
    # 方法1: 直接尝试解析整个响应为JSON
    try:
        data = json.loads(response.strip())
        if "message_times" in data and isinstance(data["message_times"], list):
            return data["message_times"]
    except json.JSONDecodeError:
        pass
    
    # 方法2: 尝试找到JSON对象的开始和结束位置
    try:
        start_pos = response.find('{')
        if start_pos != -1:
            # 找到匹配的闭括号
            open_count = 0
            for i in range(start_pos, len(response)):
                if response[i] == '{':
                    open_count += 1
                elif response[i] == '}':
                    open_count -= 1
                    if open_count == 0:
                        # 找到完整的JSON对象
                        json_str = response[start_pos:i+1]
                        data = json.loads(json_str)
                        if "message_times" in data and isinstance(data["message_times"], list):
                            return data["message_times"]
                        break
    except (json.JSONDecodeError, IndexError):
        pass
    
    # 方法3: 使用正则表达式提取时间信息
    hour_pattern = r'"hour"\s*:\s*(\d+)'
    minute_pattern = r'"minute"\s*:\s*(\d+)'
    reason_pattern = r'"reason"\s*:\s*"([^"]*)"'
    
    hours = re.findall(hour_pattern, response)
    minutes = re.findall(minute_pattern, response)
    reasons = re.findall(reason_pattern, response)
    
    # 如果找到了小时和分钟，尝试组合它们
    if hours and minutes:
        for i in range(min(len(hours), len(minutes))):
            try:
                hour = int(hours[i])
                minute = int(minutes[i])
                reason = reasons[i] if i < len(reasons) else "未提供原因"
                
                if 0 <= hour <= 23 and 0 <= minute <= 59:
                    message_times.append({
                        "hour": hour,
                        "minute": minute,
                        "reason": reason
                    })
            except (ValueError, IndexError):
                continue
    
    # 方法4: 尝试从文本中提取时间表达式
    if not message_times:
        time_patterns = [
            r'(\d{1,2})[点时:：](\d{1,2})?',  # 中文时间格式 "14点30" 或 "14:30"
            r'(\d{1,2}):(\d{1,2})',           # 英文时间格式 "14:30"
            r'(\d{1,2}) *[时点] *(\d{1,2})? *[分]?'  # 带空格的中文时间 "14 点 30 分"
        ]
        
        for pattern in time_patterns:
            times = re.findall(pattern, response)
            for time_match in times:
                try:
                    hour = int(time_match[0])
                    # 如果分钟为空，设为0
                    minute = int(time_match[1]) if time_match[1] else 0
                    
                    if 0 <= hour <= 23 and 0 <= minute <= 59:
                        # 尝试提取这个时间附近的原因
                        time_str = f"{hour}[点时:：]{minute}" if minute else f"{hour}[点时]"
                        context_start = max(0, response.find(time_str) - 50)
                        context_end = min(len(response), response.find(time_str) + 50)
                        context = response[context_start:context_end]
                        
                        message_times.append({
                            "hour": hour,
                            "minute": minute,
                            "reason": f"从上下文推断: {context}"
                        })
                except (ValueError, IndexError):
                    continue
    
    return message_times

# 发送主动消息
async def send_proactive_message(context: ContextTypes.DEFAULT_TYPE, user_id: str, reason: str):
    """发送主动消息"""
    try:
        # 生成消息内容
        message_content = await generate_message_content(
            user_id=user_id,
            reason=reason,
            system_prompt=PROACTIVE_AGENT_SYSTEM_PROMPT,
            save_to_history=False,  # 不保存这个生成过程到用户的对话历史
            model=PROACTIVE_AGENT_MODEL  # 使用指定的模型
        )
        
        # 发送消息给用户
        sent_message = await context.bot.send_message(
            chat_id=user_id, 
            text=message_content
        )
        
        # 将这条消息添加到对话历史中
        # 这里我们直接使用robot的方法来添加消息到历史
        robot = get_robot()
        robot.add_to_history(str(user_id), "assistant", message_content)
        
        logging.info(f"已发送主动消息给用户 {user_id}")
        
    except Exception as e:
        logging.error(f"发送主动消息失败: {str(e)}")

# 手动触发消息规划（用于测试）
async def trigger_message_planning(context: ContextTypes.DEFAULT_TYPE):
    """手动触发消息规划，用于测试"""
    await plan_daily_messages(context)
    return "已触发消息规划"

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

# 初始化主动消息功能
def init_proactive_messaging(application):
    """初始化主动消息功能"""
    if PROACTIVE_AGENT_ENABLED:
        # 检查是否配置了管理员ID
        admin_ids = get_admin_ids()
        if not admin_ids:
            logging.warning("未配置管理员ID，主动消息功能将不可用")
            return False
        
        # 记录使用的模型
        model_info = f"，使用模型: {PROACTIVE_AGENT_MODEL}" if PROACTIVE_AGENT_MODEL else ""
        
        # 每天凌晨1点规划当天的消息
        application.job_queue.run_daily(
            plan_daily_messages,
            time=datetime.time(hour=1, minute=0),
            name="daily_message_planning"
        )
        
        # 应用启动时也规划一次（如果当天还没规划过）
        application.job_queue.run_once(
            plan_daily_messages,
            when=1,  # 1秒后执行
            name="initial_message_planning"
        )
        
        logging.info(f"已启用主动消息功能，将向管理员 {', '.join(admin_ids)} 发送消息{model_info}")
        return True
    return False
