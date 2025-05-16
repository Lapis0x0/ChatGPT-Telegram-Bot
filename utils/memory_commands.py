import logging
import traceback
import json
from telegram import Update
from telegram.ext import ContextTypes
from utils.memory_system_functions import FunctionCallingMemorySystem

async def list_new_memories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """列出用户的所有记忆（使用新的基于函数调用的记忆系统）"""
    try:
        chatid = update.effective_chat.id
        user_id = str(update.effective_user.id)
        
        # 发送处理中消息
        processing_message = await context.bot.send_message(
            chat_id=chatid,
            text="正在获取您的记忆列表...",
            reply_to_message_id=update.message.message_id
        )
        
        # 创建记忆系统实例
        memory_system = FunctionCallingMemorySystem(user_id)
        result = memory_system.process_function_call("list_memories", {"max_results": 20, "min_importance": 1})
        
        if result["status"] == "success" and result["memories"]:
            # 格式化记忆列表
            memories_text = "🧠 **您的记忆列表**：\n\n"
            for i, memory in enumerate(result["memories"], 1):
                # 提取基本信息
                content = memory["content"]
                importance = "⭐" * memory["importance"]
                created_at = memory.get("created_at", "未知时间")
                
                # 添加标签信息（如果有）
                tags = ""
                if "tags" in memory and memory["tags"]:
                    tags = f"🏷️ {', '.join(memory['tags'])}\n"
                
                # 添加到输出文本
                memories_text += f"{i}. ID: `{memory['id']}` {importance}\n" \
                               f"📝 {content}\n" \
                               f"📅 {created_at}\n" \
                               f"{tags}\n"
            
            # 分段发送，避免消息过长
            await context.bot.edit_message_text(
                chat_id=chatid,
                message_id=processing_message.message_id,
                text=memories_text,
                parse_mode="Markdown"
            )
        else:
            # 没有记忆或出错
            message = "您目前没有任何记忆。"
            if result["status"] == "error":
                message = f"获取记忆时出错：{result['message']}"
                
            await context.bot.edit_message_text(
                chat_id=chatid,
                message_id=processing_message.message_id,
                text=message
            )
    except Exception as e:
        logging.error(f"列出记忆时出错: {str(e)}")
        logging.error(traceback.format_exc())
        
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"获取记忆列表时出错：{str(e)}",
            reply_to_message_id=update.message.message_id
        )

async def add_new_memory(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """添加新记忆（使用新的基于函数调用的记忆系统）"""
    try:
        chatid = update.effective_chat.id
        user_id = str(update.effective_user.id)
        
        # 获取命令参数
        message_text = update.message.text
        args = message_text.split(" ", 1)
        
        if len(args) < 2:
            await context.bot.send_message(
                chat_id=chatid,
                text="使用方法：/new_memory 要记住的内容\n可选：添加重要性等级(1-5)，例如：/new_memory [3] 这是重要的记忆",
                reply_to_message_id=update.message.message_id
            )
            return
            
        # 解析内容和重要性
        content = args[1]
        importance = 2  # 默认重要性
        
        # 检查是否包含重要性等级 [数字]
        if content.startswith("[") and "]" in content:
            importance_str = content[1:content.find("]")]
            if importance_str.isdigit():
                importance = int(importance_str)
                importance = max(1, min(importance, 5))  # 限制在1-5之间
                content = content[content.find("]")+1:].strip()
        
        # 发送处理中消息
        processing_message = await context.bot.send_message(
            chat_id=chatid,
            text="正在添加记忆...",
            reply_to_message_id=update.message.message_id
        )
        
        # 创建记忆系统实例并添加记忆
        memory_system = FunctionCallingMemorySystem(user_id)
        result = memory_system.process_function_call("create_memory", {
            "content": content,
            "importance": importance,
            "tags": []  # 暂不支持通过命令添加标签
        })
        
        if result["status"] == "success":
            await context.bot.edit_message_text(
                chat_id=chatid,
                message_id=processing_message.message_id,
                text=f"✅ 记忆已成功添加！\n\n📝 {content}\n🌟 重要性：{importance}/5"
            )
        else:
            await context.bot.edit_message_text(
                chat_id=chatid,
                message_id=processing_message.message_id,
                text=f"❌ 添加记忆失败：{result['message']}"
            )
    except Exception as e:
        logging.error(f"添加记忆时出错: {str(e)}")
        logging.error(traceback.format_exc())
        
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"添加记忆时出错：{str(e)}",
            reply_to_message_id=update.message.message_id
        )

async def delete_new_memory(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """删除指定记忆（使用新的基于函数调用的记忆系统）"""
    try:
        chatid = update.effective_chat.id
        user_id = str(update.effective_user.id)
        
        # 获取命令参数
        message_text = update.message.text
        args = message_text.split(" ", 1)
        
        if len(args) < 2 or not args[1].isdigit():
            await context.bot.send_message(
                chat_id=chatid,
                text="使用方法：/forget_new 记忆ID\n记忆ID可以通过 /new_memories 命令查看",
                reply_to_message_id=update.message.message_id
            )
            return
            
        memory_id = int(args[1])
        
        # 发送处理中消息
        processing_message = await context.bot.send_message(
            chat_id=chatid,
            text=f"正在删除ID为{memory_id}的记忆...",
            reply_to_message_id=update.message.message_id
        )
        
        # 创建记忆系统实例并删除记忆
        memory_system = FunctionCallingMemorySystem(user_id)
        result = memory_system.process_function_call("delete_memory", {"memory_id": memory_id})
        
        if result["status"] == "success":
            await context.bot.edit_message_text(
                chat_id=chatid,
                message_id=processing_message.message_id,
                text=f"✅ 成功删除ID为{memory_id}的记忆！"
            )
        else:
            await context.bot.edit_message_text(
                chat_id=chatid,
                message_id=processing_message.message_id,
                text=f"❌ 删除记忆失败：{result['message']}"
            )
    except Exception as e:
        logging.error(f"删除记忆时出错: {str(e)}")
        logging.error(traceback.format_exc())
        
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"删除记忆时出错：{str(e)}",
            reply_to_message_id=update.message.message_id
        )
