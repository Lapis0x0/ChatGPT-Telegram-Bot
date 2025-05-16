import os
import json
import logging
import traceback
from datetime import datetime
import pytz
from typing import List, Dict, Any, Optional, Union
from .memory_system import MemorySystem, MEMORY_DIR

# 定义东八区时区
CHINA_TZ = pytz.timezone('Asia/Shanghai')

class FunctionCallingMemorySystem:
    """
    基于Function Calling的记忆系统
    允许模型主动创建、检索和管理记忆
    """
    
    def __init__(self, user_id: str):
        """初始化记忆系统"""
        self.user_id = user_id
        self.memory_system = MemorySystem(user_id)
        
    def get_memory_functions(self) -> List[Dict[str, Any]]:
        """获取记忆系统的function定义，用于Gemini模型的function calling"""
        return [
            {
                "name": "create_memory",
                "description": "创建一条新的记忆，用于记录重要的用户信息、偏好、日期等",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string", "description": "记忆内容"},
                        "importance": {"type": "integer", "description": "重要性(1-5)，越高越重要"},
                        "tags": {"type": "array", "items": {"type": "string"}, "description": "记忆标签，用于分类和检索"}
                    },
                    "required": ["content"]
                }
            },
            {
                "name": "retrieve_memories",
                "description": "检索与当前上下文相关的记忆",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "检索查询"},
                        "max_results": {"type": "integer", "description": "最大返回结果数"},
                        "min_importance": {"type": "integer", "description": "最小重要性"}
                    },
                    "required": ["query"]
                }
            },
            {
                "name": "update_memory",
                "description": "更新已存在的记忆",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "memory_id": {"type": "integer", "description": "需要更新的记忆ID"},
                        "content": {"type": "string", "description": "新的记忆内容"},
                        "importance": {"type": "integer", "description": "新的重要性"},
                        "tags": {"type": "array", "items": {"type": "string"}, "description": "新的记忆标签"}
                    },
                    "required": ["memory_id"]
                }
            },
            {
                "name": "delete_memory",
                "description": "删除一条记忆",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "memory_id": {"type": "integer", "description": "需要删除的记忆ID"}
                    },
                    "required": ["memory_id"]
                }
            },
            {
                "name": "list_memories",
                "description": "列出用户的所有记忆",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "max_results": {"type": "integer", "description": "最大返回结果数"},
                        "min_importance": {"type": "integer", "description": "最小重要性"}
                    }
                }
            }
        ]
    
    def process_function_call(self, function_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """
        处理来自模型的function调用
        
        参数:
            function_name: 函数名称
            arguments: 函数参数
            
        返回:
            函数调用结果
        """
        try:
            if function_name == "create_memory":
                return self._create_memory(arguments)
            elif function_name == "retrieve_memories":
                return self._retrieve_memories(arguments)
            elif function_name == "update_memory":
                return self._update_memory(arguments)
            elif function_name == "delete_memory":
                return self._delete_memory(arguments)
            elif function_name == "list_memories":
                return self._list_memories(arguments)
            else:
                return {"status": "error", "message": f"未知的记忆系统函数: {function_name}"}
        except Exception as e:
            logging.error(f"处理记忆系统函数调用时出错: {str(e)}")
            logging.error(traceback.format_exc())
            return {"status": "error", "message": f"处理记忆系统函数调用时出错: {str(e)}"}
    
    def _create_memory(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """创建新记忆"""
        try:
            content = arguments.get("content", "")
            if not content:
                return {"status": "error", "message": "记忆内容不能为空"}
                
            importance = min(max(int(arguments.get("importance", 2)), 1), 5)
            tags = arguments.get("tags", [])
            
            # 添加到现有记忆系统
            success = self.memory_system.add_memory(content, importance, source="model_function_call")
            if not success:
                return {"status": "error", "message": "创建记忆失败"}
            
            # 如果提供了标签，更新记忆包含标签
            if tags and success:
                # 获取刚创建的记忆ID
                memories = self.memory_system.memories["memories"]
                if memories:
                    # 找到刚添加的记忆（应该是最后一个）
                    for memory in reversed(memories):
                        if memory["content"] == content:
                            memory_id = memory["id"]
                            # 更新记忆添加标签
                            self._update_memory_tags(memory_id, tags)
                            break
            
            return {
                "status": "success", 
                "message": "记忆已成功创建", 
                "memory": {
                    "content": content,
                    "importance": importance,
                    "tags": tags
                }
            }
        except Exception as e:
            logging.error(f"创建记忆时出错: {str(e)}")
            logging.error(traceback.format_exc())
            return {"status": "error", "message": f"创建记忆时出错: {str(e)}"}
    
    def _retrieve_memories(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """检索记忆"""
        try:
            query = arguments.get("query", "")
            max_results = min(int(arguments.get("max_results", 5)), 10)  # 限制最大结果数
            min_importance = min(max(int(arguments.get("min_importance", 1)), 1), 5)
            
            # 获取所有记忆
            all_memories = self.memory_system.get_memories(max_count=100, min_importance=min_importance)
            
            # 简单相似度匹配 (未来可以换成更高级的语义搜索)
            matched_memories = []
            for memory in all_memories:
                # 计算简单的相似度 (包含关键词)
                query_terms = query.lower().split()
                content_lower = memory["content"].lower()
                
                # 检查标签匹配 (如果记忆有标签)
                tag_match = False
                if "tags" in memory:
                    for tag in memory.get("tags", []):
                        if any(term in tag.lower() for term in query_terms):
                            tag_match = True
                            break
                
                # 检查内容匹配
                content_match = any(term in content_lower for term in query_terms)
                
                # 如果任一匹配，添加到结果中
                if content_match or tag_match:
                    matched_memories.append(memory)
            
            # 按重要性排序
            matched_memories.sort(key=lambda x: x["importance"], reverse=True)
            
            # 限制结果数量
            matched_memories = matched_memories[:max_results]
            
            return {
                "status": "success",
                "query": query,
                "memories": matched_memories,
                "total": len(matched_memories)
            }
        except Exception as e:
            logging.error(f"检索记忆时出错: {str(e)}")
            logging.error(traceback.format_exc())
            return {"status": "error", "message": f"检索记忆时出错: {str(e)}"}
    
    def _update_memory(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """更新已存在的记忆"""
        try:
            memory_id = arguments.get("memory_id")
            if memory_id is None:
                return {"status": "error", "message": "必须提供记忆ID"}
            
            # 加载用户的所有记忆
            memory_file = os.path.join(MEMORY_DIR, f"memory_{self.user_id}.json")
            if not os.path.exists(memory_file):
                return {"status": "error", "message": "记忆文件不存在"}
            
            with open(memory_file, 'r', encoding='utf-8') as f:
                memory_data = json.load(f)
            
            # 查找指定ID的记忆
            memory_found = False
            for i, memory in enumerate(memory_data["memories"]):
                if memory["id"] == memory_id:
                    # 更新记忆内容和重要性
                    if "content" in arguments:
                        memory_data["memories"][i]["content"] = arguments["content"]
                    
                    if "importance" in arguments:
                        importance = min(max(int(arguments["importance"]), 1), 5)
                        memory_data["memories"][i]["importance"] = importance
                    
                    if "tags" in arguments:
                        memory_data["memories"][i]["tags"] = arguments["tags"]
                    
                    # 更新修改时间
                    timestamp = datetime.now(CHINA_TZ).strftime("%Y-%m-%d %H:%M:%S")
                    memory_data["memories"][i]["updated_at"] = timestamp
                    memory_data["last_updated"] = timestamp
                    
                    memory_found = True
                    updated_memory = memory_data["memories"][i]
                    break
            
            if not memory_found:
                return {"status": "error", "message": f"未找到ID为{memory_id}的记忆"}
            
            # 保存更新后的记忆
            with open(memory_file, 'w', encoding='utf-8') as f:
                json.dump(memory_data, f, ensure_ascii=False, indent=2)
            
            return {
                "status": "success",
                "message": "记忆已成功更新",
                "memory": updated_memory
            }
            
        except Exception as e:
            logging.error(f"更新记忆时出错: {str(e)}")
            logging.error(traceback.format_exc())
            return {"status": "error", "message": f"更新记忆时出错: {str(e)}"}
    
    def _delete_memory(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """删除记忆"""
        try:
            memory_id = arguments.get("memory_id")
            if memory_id is None:
                return {"status": "error", "message": "必须提供记忆ID"}
            
            # 使用已有的forget_memory方法
            success = self.memory_system.forget_memory(memory_id)
            
            if success:
                return {
                    "status": "success",
                    "message": f"ID为{memory_id}的记忆已成功删除"
                }
            else:
                return {"status": "error", "message": f"未找到ID为{memory_id}的记忆或删除失败"}
                
        except Exception as e:
            logging.error(f"删除记忆时出错: {str(e)}")
            logging.error(traceback.format_exc())
            return {"status": "error", "message": f"删除记忆时出错: {str(e)}"}
    
    def _list_memories(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """列出用户的所有记忆"""
        try:
            max_results = min(int(arguments.get("max_results", 20)), 50)  # 限制最大结果数
            min_importance = min(max(int(arguments.get("min_importance", 1)), 1), 5)
            
            # 获取所有记忆
            memories = self.memory_system.get_memories(max_count=max_results, min_importance=min_importance)
            
            return {
                "status": "success",
                "memories": memories,
                "total": len(memories)
            }
            
        except Exception as e:
            logging.error(f"列出记忆时出错: {str(e)}")
            logging.error(traceback.format_exc())
            return {"status": "error", "message": f"列出记忆时出错: {str(e)}"}
    
    def _update_memory_tags(self, memory_id: int, tags: List[str]) -> bool:
        """更新记忆的标签"""
        try:
            # 加载用户的所有记忆
            memory_file = os.path.join(MEMORY_DIR, f"memory_{self.user_id}.json")
            if not os.path.exists(memory_file):
                return False
            
            with open(memory_file, 'r', encoding='utf-8') as f:
                memory_data = json.load(f)
            
            # 查找指定ID的记忆
            memory_found = False
            for i, memory in enumerate(memory_data["memories"]):
                if memory["id"] == memory_id:
                    # 更新标签
                    memory_data["memories"][i]["tags"] = tags
                    memory_found = True
                    break
            
            if not memory_found:
                return False
            
            # 保存更新后的记忆
            with open(memory_file, 'w', encoding='utf-8') as f:
                json.dump(memory_data, f, ensure_ascii=False, indent=2)
            
            return True
            
        except Exception as e:
            logging.error(f"更新记忆标签时出错: {str(e)}")
            return False
