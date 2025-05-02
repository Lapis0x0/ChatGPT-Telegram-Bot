import os
import json
import logging
from datetime import datetime
import pytz

# 定义东八区时区
CHINA_TZ = pytz.timezone('Asia/Shanghai')

# 从环境变量获取系统提示词，并确保包含东八区日期和时间
current_datetime = datetime.now(CHINA_TZ)
current_date = current_datetime.strftime('%Y-%m-%d')
current_time = current_datetime.strftime('%H:%M')
DEFAULT_SYSTEM_PROMPT = os.environ.get('SYSTEMPROMPT', f"你是一个有帮助的AI助手。当前日期和时间（东八区）：{current_date} {current_time}")

# 获取东八区当前时间
def get_china_time():
    """获取中国时区（东八区）的当前时间"""
    return datetime.now(pytz.UTC).astimezone(CHINA_TZ)

# 记忆存储路径 - 修改为使用Docker容器内的路径
# 在Docker容器中，/home/user_configs是挂载的持久化目录
if os.path.exists('/home/user_configs'):
    # Docker容器内路径
    USER_CONFIGS_DIR = '/home/user_configs'
else:
    # 本地开发环境路径
    USER_CONFIGS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "user_configs")

MEMORY_DIR = os.path.join(USER_CONFIGS_DIR, "memories")
os.makedirs(MEMORY_DIR, exist_ok=True)

class MemorySystem:
    def __init__(self, user_id):
        self.user_id = user_id
        self.memory_file = os.path.join(MEMORY_DIR, f"memory_{user_id}.json")
        self.memories = self._load_memories()
        
    def _load_memories(self):
        """加载用户的记忆"""
        if os.path.exists(self.memory_file):
            try:
                with open(self.memory_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logging.error(f"加载记忆文件失败: {str(e)}")
                return {"memories": [], "last_updated": None}
        else:
            return {"memories": [], "last_updated": None}
    
    def _save_memories(self):
        """保存用户的记忆"""
        try:
            with open(self.memory_file, 'w', encoding='utf-8') as f:
                json.dump(self.memories, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logging.error(f"保存记忆文件失败: {str(e)}")
    
    def add_memory(self, content, importance=1, source="conversation"):
        """添加新记忆
        
        参数：
            content: 记忆内容
            importance: 重要性 (1-5)，数字越大越重要
            source: 记忆来源 (conversation, user_input, system)
        """
        timestamp = get_china_time().strftime("%Y-%m-%d %H:%M:%S")
        
        # 检查是否已存在类似记忆，避免重复
        for memory in self.memories["memories"]:
            if self._calculate_similarity(content, memory["content"]) > 0.8:
                # 更新已有记忆
                memory["updated_at"] = timestamp
                memory["importance"] = max(memory["importance"], importance)
                memory["access_count"] += 1
                self.memories["last_updated"] = timestamp
                self._save_memories()
                return True
        
        # 添加新记忆
        new_memory = {
            "id": len(self.memories["memories"]) + 1,
            "content": content,
            "created_at": timestamp,
            "updated_at": timestamp,
            "importance": importance,
            "access_count": 1,
            "source": source
        }
        
        self.memories["memories"].append(new_memory)
        self.memories["last_updated"] = timestamp
        self._save_memories()
        return True
    
    def get_memories(self, max_count=10, min_importance=1):
        """获取记忆
        
        参数：
            max_count: 最大返回数量
            min_importance: 最小重要性
        
        返回：
            记忆列表
        """
        # 按重要性和更新时间排序
        sorted_memories = sorted(
            [m for m in self.memories["memories"] if m["importance"] >= min_importance],
            key=lambda x: (x["importance"], x["updated_at"]),
            reverse=True
        )
        
        return sorted_memories[:max_count]
    
    def forget_memory(self, memory_id):
        """删除指定记忆"""
        self.memories["memories"] = [m for m in self.memories["memories"] if m["id"] != memory_id]
        self.memories["last_updated"] = get_china_time().strftime("%Y-%m-%d %H:%M:%S")
        self._save_memories()
    
    def generate_memory_prompt(self, max_memories=5, system_prompt=None):
        """生成包含记忆的系统提示词
        
        参数：
            max_memories: 最大记忆数量
            system_prompt: 系统提示词，如果为None则使用默认值
        
        返回：
            增强了记忆的系统提示词
        """
        memories = self.get_memories(max_count=max_memories, min_importance=2)
        
        # 如果没有提供系统提示词，使用默认值
        if system_prompt is None:
            system_prompt = DEFAULT_SYSTEM_PROMPT
        
        if not memories:
            return system_prompt
        
        memory_text = "以下是我之前了解到的关于你的重要信息：\n\n"
        for idx, memory in enumerate(memories, 1):
            memory_text += f"{idx}. {memory['content']}\n"
        memory_text += "\n请在我们的对话中记住这些信息，但不要主动提及你在'记忆'这些内容。"
        
        # 将记忆添加到系统提示词中
        enhanced_prompt = f"{system_prompt}\n\n{memory_text}"
        return enhanced_prompt
    
    def _calculate_similarity(self, text1, text2):
        """计算两段文本的相似度（简化版）"""
        # 这是一个简化的实现，实际应用中可以使用更先进的方法
        words1 = set(text1.lower().split())
        words2 = set(text2.lower().split())
        
        if not words1 or not words2:
            return 0.0
        
        intersection = words1.intersection(words2)
        union = words1.union(words2)
        
        return len(intersection) / len(union)

# 记忆分析器
class MemoryAnalyzer:
    """分析对话内容，提取可能需要记忆的信息"""
    
    @staticmethod
    def analyze_message(message, user_id):
        """分析消息，提取需要记忆的信息"""
        memory_system = MemorySystem(user_id)
        
        # 提取用户偏好
        preferences = MemoryAnalyzer._extract_preferences(message)
        for pref in preferences:
            memory_system.add_memory(pref, importance=3, source="user_input")
        
        # 提取重要事实
        facts = MemoryAnalyzer._extract_facts(message)
        for fact in facts:
            memory_system.add_memory(fact, importance=2, source="conversation")
        
        # 提取重要日期
        dates = MemoryAnalyzer._extract_dates(message)
        for date_info in dates:
            memory_system.add_memory(date_info, importance=4, source="user_input")
        
        # 返回是否添加了新记忆
        return len(preferences) + len(facts) + len(dates) > 0
    
    @staticmethod
    def _extract_preferences(message):
        """提取用户偏好"""
        preferences = []
        
        # 喜欢/不喜欢的模式
        like_patterns = [
            "我喜欢", "我爱", "我偏好", "我享受", 
            "我不喜欢", "我讨厌", "我不想", "我不要"
        ]
        
        message_lower = message.lower()
        for pattern in like_patterns:
            if pattern in message_lower:
                # 简化处理，实际上可以使用更复杂的NLP方法
                start_idx = message_lower.find(pattern)
                if start_idx != -1:
                    # 尝试提取完整的偏好表达
                    end_idx = message_lower.find("。", start_idx)
                    if end_idx == -1:
                        end_idx = len(message_lower)
                    preference = message[start_idx:end_idx].strip()
                    preferences.append(preference)
        
        return preferences
    
    @staticmethod
    def _extract_facts(message):
        """提取重要事实"""
        facts = []
        
        # 表明是重要信息的关键词
        fact_indicators = [
            "我是", "我的", "我有", "我住在", "我工作在",
            "我的生日是", "我的家人", "我的父母", "我的孩子",
            "我的工作是", "我的电话是", "我的邮箱是"
        ]
        
        message_lower = message.lower()
        for indicator in fact_indicators:
            if indicator in message_lower:
                start_idx = message_lower.find(indicator)
                if start_idx != -1:
                    end_idx = message_lower.find("。", start_idx)
                    if end_idx == -1:
                        end_idx = len(message_lower)
                    fact = message[start_idx:end_idx].strip()
                    facts.append(fact)
        
        return facts
    
    @staticmethod
    def _extract_dates(message):
        """提取重要日期信息"""
        dates = []
        
        # 日期关键词
        date_indicators = [
            "生日", "周年", "纪念日", "节日", 
            "开始", "结束", "期限", "截止日期"
        ]
        
        message_lower = message.lower()
        for indicator in date_indicators:
            if indicator in message_lower:
                start_idx = max(0, message_lower.find(indicator) - 20)
                end_idx = min(len(message_lower), message_lower.find(indicator) + 20)
                date_context = message[start_idx:end_idx].strip()
                dates.append(date_context)
        
        return dates

# 辅助函数：使用AI帮助分析需要记忆的内容
async def analyze_with_ai(user_id, message, robot):
    """使用AI分析消息中需要记忆的内容"""
    
    analyze_prompt = """
    分析以下消息，提取需要记忆的重要信息。
    仅提取以下类型的信息：
    1. 用户的偏好和喜好
    2. 用户的个人信息（如生日、职业等）
    3. 重要日期和事件
    4. 用户明确要求记住的事情
    
    仅返回一个JSON格式的结果，格式如下：
    {
        "memories": [
            {"content": "记忆内容1", "importance": 重要性(1-5)},
            {"content": "记忆内容2", "importance": 重要性(1-5)}
        ]
    }
    
    如果没有需要记忆的内容，返回空列表。
    """
    
    try:
        # 创建临时会话ID
        temp_convo_id = f"memory_analysis_{user_id}_{get_china_time().strftime('%Y%m%d%H%M%S')}"
        
        # 添加分析提示和用户消息到临时会话
        robot.add_to_conversation(analyze_prompt, "system", temp_convo_id)
        robot.add_to_conversation(message, "user", temp_convo_id)
        
        # 使用AI分析
        response = ""
        async for data in robot.ask_stream_async(message, convo_id=temp_convo_id):
            if isinstance(data, str):
                response += data
        
        # 尝试解析JSON响应
        try:
            result = json.loads(response)
            memory_system = MemorySystem(user_id)
            
            # 添加AI识别的记忆
            memories_added = 0
            if "memories" in result and isinstance(result["memories"], list):
                for memory in result["memories"]:
                    if "content" in memory and memory["content"].strip():
                        importance = int(memory.get("importance", 2))
                        memory_system.add_memory(
                            memory["content"], 
                            importance=min(max(importance, 1), 5),
                            source="ai_analysis"
                        )
                        memories_added += 1
            
            return memories_added > 0
            
        except json.JSONDecodeError:
            logging.warning(f"AI返回的记忆分析结果无法解析为JSON: {response}")
            return False
            
    except Exception as e:
        logging.error(f"使用AI分析记忆内容时出错: {str(e)}")
        return False
