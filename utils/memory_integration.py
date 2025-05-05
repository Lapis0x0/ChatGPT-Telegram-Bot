import logging
import asyncio
import os
from utils.memory_system import MemorySystem, MemoryAnalyzer, analyze_with_ai
from datetime import datetime

# 最大尝试次数
MAX_RETRY = 3

# 对话计数器和历史记录
conversation_counters = {}  # 用户ID -> 对话计数
conversation_history = {}   # 用户ID -> 最近15轮对话列表
SUMMARY_INTERVAL = 15       # 每15轮对话总结一次

async def process_memory(user_id, message, robot):
    """处理用户消息，分析并保存重要信息到长期记忆
    
    参数：
        user_id: 用户ID
        message: 用户消息
        robot: 机器人实例
        
    返回：
        bool: 是否添加了新记忆
    """
    try:
        # 首先使用简单规则分析
        simple_analysis_result = MemoryAnalyzer.analyze_message(message, user_id)
        
        # 如果简单规则没有找到记忆，尝试使用AI分析
        if not simple_analysis_result:
            # 使用AI分析，但不要阻塞主对话流程
            asyncio.create_task(analyze_with_ai(user_id, message, robot))
            
        return simple_analysis_result
    except Exception as e:
        logging.error(f"处理记忆时出错: {str(e)}")
        return False

def get_memory_enhanced_prompt(user_id, system_prompt=None):
    """获取增强了记忆的系统提示词
    
    参数：
        user_id: 用户ID
        system_prompt: 原始系统提示词，如果为None则使用默认系统提示词
        
    返回：
        str: 增强了记忆的系统提示词
    """
    try:
        memory_system = MemorySystem(user_id)
        return memory_system.generate_memory_prompt(max_memories=5, system_prompt=system_prompt)
    except Exception as e:
        logging.error(f"获取增强记忆提示词时出错: {str(e)}")
        # 如果出错，返回原始系统提示词，或者默认提示词
        return system_prompt or "你是一个有帮助的AI助手。"

async def add_explicit_memory(user_id, content, importance=3):
    """用户明确要求添加的记忆
    
    参数：
        user_id: 用户ID
        content: 记忆内容
        importance: 重要性 (1-5)
        
    返回：
        bool: 是否成功添加
    """
    try:
        memory_system = MemorySystem(user_id)
        result = memory_system.add_memory(
            content=content, 
            importance=importance, 
            source="user_explicit"
        )
        return result
    except Exception as e:
        logging.error(f"添加明确记忆时出错: {str(e)}")
        return False

async def list_memories(user_id, max_count=10):
    """列出用户的记忆
    
    参数：
        user_id: 用户ID
        max_count: 最大显示数量
        
    返回：
        str: 格式化的记忆列表
    """
    try:
        memory_system = MemorySystem(user_id)
        memories = memory_system.get_memories(max_count=max_count)
        
        if not memories:
            return "您目前没有保存的记忆。"
        
        result = "以下是您的长期记忆：\n\n"
        for idx, memory in enumerate(memories, 1):
            created_at = memory.get("created_at", "未知时间")
            importance = "⭐" * memory.get("importance", 1)
            result += f"{idx}. {memory['content']} {importance}\n   添加于: {created_at}\n\n"
            
        return result
    except Exception as e:
        logging.error(f"列出记忆时出错: {str(e)}")
        return "获取记忆列表时出错。"

async def forget_memory(user_id, memory_id):
    """删除指定的记忆
    
    参数：
        user_id: 用户ID
        memory_id: 记忆ID
        
    返回：
        bool: 是否成功删除
    """
    try:
        memory_system = MemorySystem(user_id)
        memory_system.forget_memory(memory_id)
        return True
    except Exception as e:
        logging.error(f"删除记忆时出错: {str(e)}")
        return False

async def forget_memories(user_id, memory_ids):
    """批量删除多个记忆
    
    参数：
        user_id: 用户ID
        memory_ids: 记忆ID列表
        
    返回：
        dict: 包含成功和失败的记忆ID
    """
    results = {
        "success": [],
        "failed": []
    }
    
    try:
        memory_system = MemorySystem(user_id)
        for memory_id in memory_ids:
            try:
                memory_system.forget_memory(memory_id)
                results["success"].append(memory_id)
            except Exception as e:
                logging.error(f"删除记忆 {memory_id} 时出错: {str(e)}")
                results["failed"].append(memory_id)
        
        return results
    except Exception as e:
        logging.error(f"批量删除记忆时出错: {str(e)}")
        return results

async def summarize_with_flash(user_id, history, robot):
    """使用Gemini Flash模型总结对话历史并提取记忆
    
    参数：
        user_id: 用户ID
        history: 对话历史列表，每项是一个字典 {"role": "user"|"assistant", "content": "消息内容"}
        robot: 机器人实例
        
    返回：
        bool: 是否成功提取并添加记忆
    """
    try:
        # 将对话历史转换为文本
        conversation_text = ""
        for msg in history:
            prefix = "用户: " if msg["role"] == "user" else "助手: "
            conversation_text += f"{prefix}{msg['content']}\n\n"
        
        # 获取当前记忆库内容
        memory_system = MemorySystem(user_id)
        current_memories = memory_system.get_memories(max_count=30)  # 获取较多现有记忆以供对比
        
        # 将当前记忆格式化为文本
        existing_memories_text = ""
        if current_memories:
            existing_memories_text = "现有记忆库内容：\n"
            for idx, memory in enumerate(current_memories, 1):
                existing_memories_text += f"{idx}. {memory['content']} (重要性: {memory['importance']})\n"
        
        # 创建临时会话ID
        temp_convo_id = f"memory_summary_{user_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}"
        
        # 获取系统提示词
        system_prompt = os.environ.get('SYSTEMPROMPT', '')
        
        # 构建提示词，包含现有记忆内容和系统提示词参考
        summary_prompt = f"""
        请分析以下最近的对话历史，提取可能需要记住的重要信息，但避免与已有记忆重复。
        
        对话历史：
        {conversation_text}
        
        {existing_memories_text}
        
        系统提示词（仅供参考，请不要提取这里已包含的用户基本信息）：
        {system_prompt}
        
        请提取以下类型的信息，但要注意：
        1. 不要提取与现有记忆重复或高度相似的内容
        2. 不要提取系统提示词中已包含的用户基本信息
        3. 只提取新的、有价值的信息，尤其是对话中特别提到的新内容
        4. 如果新信息是对已有记忆的补充或更新，请明确指出
        
        提取信息类型：
        1. 用户新提到的偏好和喜好
        2. 用户新提到的个人信息
        3. 重要日期和事件
        4. 用户明确要求记住的事情
        5. 对话中多次提及的重要主题
        
        对于每条提取的信息，请按以下格式返回JSON：
        {{
            "memories": [
                {{"content": "具体记忆内容，使用第三人称描述", "importance": 重要性(1-5)}},
                ...
            ]
        }}
        
        返回的重要性评分标准：
        - 5: 极其重要（如生日、重要关系、明确要求记住的事）
        - 4: 非常重要（强烈偏好、重要事件）
        - 3: 比较重要（明确表达的喜好）
        - 2: 一般重要（提及过的兴趣爱好）
        - 1: 略微重要（可能有用的背景信息）
        
        如果没有新的值得记忆的重要信息，请返回空列表。
        """
        
        # 指定使用Gemini Flash模型
        model_name = "gemini-2.5-flash-preview-04-17"
        
        # 获取API设置
        api_key = os.environ.get('GOOGLE_AI_API_KEY')
        if not api_key:
            logging.error("未设置 GOOGLE_AI_API_KEY，无法使用 Gemini Flash 模型")
            return False
            
        api_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:streamGenerateContent?key={api_key}"
        
        # 使用现有的robot实例，而不是创建新的Chatbot实例
        # 添加提示词到临时会话
        robot.add_to_conversation(summary_prompt, "user", temp_convo_id)
        
        # 使用Gemini Flash分析
        response = ""
        async for data in robot.ask_stream_async(
            summary_prompt, 
            convo_id=temp_convo_id,
            model=model_name,
            api_key=api_key,
            api_url=api_url
        ):
            if isinstance(data, str):
                response += data
        
        # 尝试解析JSON响应
        try:
            import json
            # 清理响应中可能包含的代码块标记
            clean_response = response
            if "```json" in clean_response:
                clean_response = clean_response.split("```json")[1]
            if "```" in clean_response:
                clean_response = clean_response.split("```")[0]
            
            # 去除可能的前后空白字符
            clean_response = clean_response.strip()
            
            # 解析JSON
            result = json.loads(clean_response)
            memory_system = MemorySystem(user_id)
            
            # 添加AI识别的记忆
            memories_added = 0
            if "memories" in result and isinstance(result["memories"], list):
                for memory in result["memories"]:
                    if "content" in memory and memory["content"].strip():
                        importance = int(memory.get("importance", 3))
                        memory_system.add_memory(
                            memory["content"], 
                            importance=min(max(importance, 1), 5),
                            source="conversation_summary"
                        )
                        memories_added += 1
                        
                logging.info(f"用户 {user_id} 的对话总结添加了 {memories_added} 条记忆")
            
            return memories_added > 0
            
        except json.JSONDecodeError:
            logging.warning(f"Gemini Flash返回的记忆分析结果无法解析为JSON: {response}")
            return False
            
    except Exception as e:
        # 捕获所有异常，确保不影响主对话流程
        logging.error(f"总结对话时出错: {str(e)}")
        return False

async def track_conversation(user_id, role, message, robot):
    """跟踪对话并在达到阈值时总结
    
    参数：
        user_id: 用户ID
        role: 发言角色 ("user" 或 "assistant")
        message: 消息内容
        robot: 机器人实例
        
    返回：
        None
    """
    try:
        # 初始化用户的对话历史和计数器
        if user_id not in conversation_history:
            conversation_history[user_id] = []
        if user_id not in conversation_counters:
            conversation_counters[user_id] = 0
        
        # 添加当前消息到历史
        conversation_history[user_id].append({
            "role": role,
            "content": message
        })
        
        # 保持历史记录不超过20轮（多保留一些以便更好地总结）
        if len(conversation_history[user_id]) > 20:
            conversation_history[user_id] = conversation_history[user_id][-20:]
        
        # 只在用户消息后增加计数
        if role == "user":
            conversation_counters[user_id] += 1
            
            # 检查是否达到总结阈值
            if conversation_counters[user_id] >= SUMMARY_INTERVAL:
                # 重置计数器
                conversation_counters[user_id] = 0
                
                # 从环境变量获取Gemini API密钥
                api_key = os.environ.get('GOOGLE_AI_API_KEY')
                if not api_key:
                    logging.error("未设置 GOOGLE_AI_API_KEY，无法进行对话总结")
                    return
                
                # 异步进行总结，不阻塞主对话流程
                asyncio.create_task(summarize_with_flash(
                    user_id, 
                    conversation_history[user_id], 
                    robot
                ))
    except Exception as e:
        # 捕获所有异常，确保不影响主对话流程
        logging.error(f"跟踪对话时出错: {str(e)}")
        # 即使出错也不抛出异常，不影响主对话

async def force_summarize_memory(user_id, robot):
    """强制触发记忆总结，用于调试
    
    参数：
        user_id: 用户ID
        robot: 机器人实例
        
    返回：
        str: 总结结果消息
    """
    try:
        # 检查是否有对话历史
        if user_id not in conversation_history or not conversation_history[user_id]:
            return "没有可用的对话历史记录可供总结。"
        
        # 从环境变量获取Gemini API密钥
        api_key = os.environ.get('GOOGLE_AI_API_KEY')
        if not api_key:
            return "未设置 GOOGLE_AI_API_KEY，无法进行对话总结。"
        
        # 执行总结
        success = await summarize_with_flash(
            user_id, 
            conversation_history[user_id], 
            robot
        )
        
        if success:
            # 获取新添加的记忆
            memory_system = MemorySystem(user_id)
            recent_memories = memory_system.get_memories(max_count=5)
            
            # 格式化最近的记忆
            result = "对话总结完成，已提取以下记忆：\n\n"
            for idx, memory in enumerate(recent_memories, 1):
                importance = "⭐" * memory.get("importance", 1)
                result += f"{idx}. {memory['content']} {importance}\n\n"
            
            return result
        else:
            return "对话总结未能提取到新的记忆。"
            
    except Exception as e:
        logging.error(f"强制总结记忆时出错: {str(e)}")
        return f"总结过程中出错: {str(e)}"
