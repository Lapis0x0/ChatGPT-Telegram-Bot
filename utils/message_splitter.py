import json
import re
import logging
import traceback
from datetime import datetime
import asyncio

# 定义消息拆分处理函数
async def process_structured_messages(message_content, context, chat_id, message_thread_id=None, reply_to_message_id=None):
    """
    处理结构化消息格式，支持模型自主拆分多条消息
    
    参数:
        message_content: 模型返回的消息内容
        context: Telegram上下文
        chat_id: 聊天ID
        message_thread_id: 消息线程ID (可选)
        reply_to_message_id: 回复消息ID (可选)
    
    返回:
        处理后的消息内容
    """
    try:
        # 记录原始消息内容
        logging.info(f"原始消息内容: {message_content[:100]}...")  # 只记录前100个字符，避免日志过长
        
        # 初始化match变量，避免未定义错误
        json_match = None
        is_json_prefix = False
        
        # 如果消息以"json"开头，尝试直接解析后面的内容
        if message_content.strip().lower().startswith("json"):
            logging.info("检测到消息以'json'开头")
            is_json_prefix = True
            json_content = message_content.strip()[4:].strip()  # 移除"json"前缀
            
            try:
                # 尝试直接解析
                json_data = json.loads(json_content)
                logging.info("成功从'json'前缀消息中解析JSON数据")
            except json.JSONDecodeError as e:
                logging.warning(f"从'json'前缀消息解析JSON失败: {str(e)}")
                
                # 尝试清理JSON字符串
                try:
                    # 查找最后一个有效的JSON结构
                    # 通常JSON结构以}结束，后面可能有额外字符
                    last_brace_index = json_content.rfind('}')
                    if last_brace_index > 0:
                        # 查找匹配的开始括号
                        first_brace_index = json_content.find('{')
                        if first_brace_index >= 0 and first_brace_index < last_brace_index:
                            # 提取可能有效的JSON部分
                            potential_json = json_content[first_brace_index:last_brace_index+1]
                            logging.info(f"尝试清理后的JSON: {potential_json[:100]}...")
                            
                            # 尝试解析清理后的JSON
                            json_data = json.loads(potential_json)
                            logging.info("成功从清理后的JSON中解析数据")
                        else:
                            json_data = None
                    else:
                        json_data = None
                except json.JSONDecodeError as e2:
                    logging.warning(f"清理后的JSON解析仍然失败: {str(e2)}")
                    json_data = None
        else:
            json_data = None
        
        # 尝试多种方式检测JSON格式
        if not json_data:
            # 方式1: 检查消息是否包含JSON代码块
            json_pattern = r'```json\s*({[\s\S]*?})\s*```'
            json_match = re.search(json_pattern, message_content)
            if json_match:
                try:
                    logging.info(f"找到JSON代码块: {json_match.group(1)[:100]}...")
                    json_data = json.loads(json_match.group(1))
                    logging.info("从JSON代码块中成功解析到JSON数据")
                except json.JSONDecodeError as e:
                    logging.warning(f"JSON代码块解析失败: {str(e)}")
            
            # 方式2: 检查消息是否整体是一个JSON对象
            if not json_data and message_content.strip().startswith('{') and message_content.strip().endswith('}'):
                try:
                    logging.info("消息整体看起来像JSON对象")
                    json_data = json.loads(message_content.strip())
                    logging.info("从整体消息中成功解析到JSON数据")
                except json.JSONDecodeError as e:
                    logging.warning(f"整体消息JSON解析失败: {str(e)}")
            
            # 方式3: 检查消息是否包含任意代码块中的JSON
            if not json_data:
                code_pattern = r'```(?:.*?)\s*({[\s\S]*?})\s*```'
                code_match = re.search(code_pattern, message_content)
                if code_match:
                    try:
                        logging.info(f"找到代码块: {code_match.group(1)[:100]}...")
                        json_data = json.loads(code_match.group(1))
                        logging.info("从代码块中成功解析到JSON数据")
                        json_match = code_match  # 更新json_match以便后续使用
                    except json.JSONDecodeError as e:
                        logging.warning(f"代码块中的JSON解析失败: {str(e)}")
            
            # 方式4: 尝试查找消息中任何看起来像JSON的部分
            if not json_data:
                json_like_pattern = r'({[\s\S]*?})'
                matches = re.finditer(json_like_pattern, message_content)
                for potential_match in matches:
                    try:
                        potential_json = potential_match.group(1)
                        logging.info(f"找到可能的JSON片段: {potential_json[:100]}...")
                        # 确保这是一个完整的JSON对象，而不仅仅是一个字典字面量
                        if '"messages"' in potential_json or "'messages'" in potential_json:
                            json_data = json.loads(potential_json)
                            if json_data and 'messages' in json_data:
                                logging.info("从JSON片段中成功解析到JSON数据")
                                json_match = potential_match  # 更新json_match以便后续使用
                                break
                    except json.JSONDecodeError:
                        continue
        
        # 如果没有找到有效的JSON数据，返回原始消息
        if not json_data or 'messages' not in json_data:
            logging.info("未检测到结构化消息格式，使用单条消息发送")
            return message_content
        
        # 如果是以json开头的格式，不需要检查JSON外是否有内容
        if is_json_prefix:
            # 直接处理JSON数据
            clean_message = ""
        else:
            # 如果JSON是嵌入在更大的消息中，尝试移除JSON部分
            clean_message = message_content
            if json_match:
                # 根据匹配到的模式移除JSON部分
                start, end = json_match.span()
                clean_message = message_content[:start] + message_content[end:]
                clean_message = clean_message.strip()
            elif message_content.strip().startswith('{') and message_content.strip().endswith('}'):
                clean_message = ""
        
        # 如果JSON外还有内容，使用单条消息发送
        if clean_message and not clean_message.isspace():
            logging.info(f"JSON外还有内容: {clean_message[:100]}...")
            return message_content
        
        # 获取消息列表
        messages = json_data['messages']
        
        if not messages:
            logging.info("消息列表为空，使用单条消息发送")
            return message_content
        
        # 如果只有一条消息，直接发送
        if len(messages) == 1:
            logging.info("只有一条消息，使用单条消息发送")
            return messages[0]['content']
        
        # 发送多条消息
        logging.info(f"检测到多条消息，共{len(messages)}条，开始拆分发送")
        
        # 发送第一条消息，并记住消息ID用于后续回复
        first_message = await context.bot.send_message(
            chat_id=chat_id,
            message_thread_id=message_thread_id,
            text=messages[0]['content'],
            reply_to_message_id=reply_to_message_id
        )
        
        # 发送后续消息，每条消息之间添加短暂延迟，模拟人类打字速度
        last_message_id = first_message.message_id
        
        for i, msg in enumerate(messages[1:], 1):
            # 计算延迟时间，基于消息长度，但设置最小延迟
            # 短消息：1.5-2.5秒
            # 中等消息：2-3秒
            # 长消息：2.5-4秒，但不超过4秒
            content_length = len(msg['content'])
            
            if content_length < 50:  # 短消息
                base_delay = 1.5
            elif content_length < 200:  # 中等消息
                base_delay = 2.0
            else:  # 长消息
                # 每增加100字符增加0.5秒，最多4秒
                base_delay = min(2.5 + (content_length - 200) / 100 * 0.5, 4.0)
            
            # 添加随机性，使延迟更自然
            import random
            delay = base_delay * (0.9 + random.random() * 0.2)  # 90%-110%的基础延迟
            
            logging.info(f"发送第{i+1}条消息，长度{content_length}字符，延迟{delay:.2f}秒")
            
            # 等待延迟时间
            await asyncio.sleep(delay)
            
            # 发送消息，使用更复杂的随机逻辑决定是否回复前一条消息
            # 考虑多种因素：消息位置、消息长度、完全随机因素
            
            # 基础随机概率 - 完全随机因素
            base_probability = random.random()
            
            # 位置因素 - 相邻消息更可能有回复关系，但不是固定的
            position_factor = 0.0
            if i == 1:  # 第二条消息
                position_factor = 0.2  # 第二条消息有额外20%概率回复第一条
            elif i == len(messages) - 1:  # 最后一条消息
                position_factor = -0.1  # 最后一条消息更可能独立
            
            # 长度因素 - 短消息更可能是对前面内容的回应
            length_factor = 0.0
            if content_length < 30:  # 非常短的消息
                length_factor = 0.15  # 短消息更可能是回复
            elif content_length > 200:  # 长消息
                length_factor = -0.1  # 长消息更可能独立
            
            # 内容启发式判断 - 如果消息以问号开头或结尾，更可能是回复
            content_factor = 0.0
            if msg['content'].strip().startswith('?') or msg['content'].strip().endswith('?'):
                content_factor = 0.15
            
            # 计算最终概率 - 基础概率在25%-45%之间浮动
            final_probability = 0.35 + position_factor + length_factor + content_factor
            
            # 随机决定是否回复
            should_reply = base_probability < final_probability
            
            # 记录日志
            logging.info(f"消息{i+1}回复决策: 基础概率={base_probability:.2f}, 最终概率={final_probability:.2f}, 是否回复={should_reply}")
            
            sent_message = await context.bot.send_message(
                chat_id=chat_id,
                message_thread_id=message_thread_id,
                text=msg['content'],
                reply_to_message_id=last_message_id if should_reply else None  # 根据复杂随机逻辑决定是否回复
            )
            
            last_message_id = sent_message.message_id
        
        logging.info("所有拆分消息发送完成")
        
        # 返回空字符串，因为消息已经发送
        return ""
        
    except Exception as e:
        logging.error(f"处理结构化消息时出错: {str(e)}")
        traceback.print_exc()
        return message_content


def get_structured_message_prompt():
    """
    获取结构化消息的提示词
    
    返回:
        str: 提示词文本
    """
    return """
如果你认为当前对话需要拆分为多条消息发送，可以使用以下JSON格式：

```json
{
  "messages": [
    {
      "content": "第一条消息内容"
    },
    {
      "content": "第二条消息内容"
    },
    ...
  ]
}
```

或者直接返回不带代码块的JSON：

{
  "messages": [
    {
      "content": "第一条消息内容"
    },
    {
      "content": "第二条消息内容"
    }
  ]
}

或者以"json"开头，后面跟着JSON内容：

json {
  "messages": [
    {
      "content": "第一条消息内容"
    },
    {
      "content": "第二条消息内容"
    }
  ]
}

这将使系统自动拆分为多条消息发送，更接近人类的对话方式。适用场景包括：
1. 表达不同的情绪或想法
2. 分享多个独立的信息点
3. 模拟思考过程
4. 自然的对话节奏转换

请根据对话内容自行判断是否需要使用此功能，如果只是正常的、简单的日常聊天对话，那么请不要使用此功能。
"""
