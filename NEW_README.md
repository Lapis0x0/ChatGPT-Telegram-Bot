# 已完成
- [x] 实现模型自决策的自动发送信息
- [x] 实现类OpenAI的前长期记忆系统

目前似乎每条对话的时间戳插入系统还稍微有点问题，等未来调试进一步观察。

# 未来计划：
## 一、记忆系统优化
目前采用的记忆机制实际上效果还不错，但可能会添加重复冗余信息。可以根据在添加信息时已经标注好的主要等级进行拟人的，有记忆衰退曲线的自动遗忘机制。

目前的自动记忆机制：系统会定期（每15轮对话）分析对话内容并提取重要信息添加到记忆库中，这样用户不需要手动记录所有重要信息。

在对话时，会根据重要性和时间选择最相关的几条（默认5条）记忆添加到系统提示词中。这样做的好处是避免系统提示词过长，同时确保最重要的上下文信息能够被模型考虑到。

```
在 memory_system.py 的 generate_memory_prompt 函数中，系统会：
获取用户的记忆列表
按重要性和更新时间排序
选择最多 max_memories 条记忆（默认为5条）
将这些记忆格式化后添加到系统提示词中
```

但这样做的缺点就是：传递的记忆数量相对较少；可能很多相对重要的信息不能被传递进去；未来可以进一步提高记忆的传递数量，并引入一个动态记忆调整机制，删去过期/不重要的记忆。

- [ ] 针对部分时效性比较强的记忆，可以在过期后自动归档/删除。
- [ ] 记忆的内容也需要传递到主动对话的上下文之中。

### 思路
1.传递给模型的记忆总量进一步增加（10条）
2.增加记忆的定期删除机制（模型自动分析删改过期的记忆+支持用户批量删除记忆）



## 二、进一步优化聊天系统

### 需求解决记录

- [x] Gemini 2.5目前还是太喜欢输出各种拼音、英文了，应当在提示词这块进行优化，减少各种逆天英文解释的输出强度

- [x] 为了让模型更好的知道当前的时间，应当在每一次对话的system prompt之前添加当前的日期（东八区）。

- [x] 目前的主动触发机制仍然不完善；主动触发的时机是在每天凌晨1点就决定了的，没办法根据用户当前的对话内容来动态调整。可以添加一个“主动对话欲望”的数值机制，如果数值积累较高，则主动发起对话的几率也会随之增高。

5.3 2:05：提交：部分实现“主动对话欲望”的数值机制；但目前仍然不完善，对话衔接不够流畅，有“为话找话”的感觉；需要进一步优化，当前提交仅作checkpoint

5.2 2:17：
这样，我决定修改模型的“主动对话意愿”机制。

现在，主动对话意愿这个数值将不会再由flash模型去评价每一次对话来增删，而是更加符合**情侣之间**的聊天热度，这一数值会随着聊天空窗期时间的增加而不断增加，如果我在一定时间内没有找模型对话，按么模型就会根据之前的对话历史主动找我。

我希望最终的机制设计大致可以实现每天4-5次的主动对话。
```
1.主动对话意愿机制重构

移除原有通过flash模型分析每次对话内容来调整欲望值的机制。
新机制下，主动对话意愿值随用户与机器人的“空窗期”自动递增，用户每次主动对话后欲望值重置。
欲望值达到阈值后，机器人会主动发起对话，目标实现每日4-5次自然主动互动。

2.上下文与时间处理保持稳定

主动消息依然会带上历史对话上下文，确保连续性。
每次对话均在系统提示词中加入东八区当前时间，时间传递逻辑未变。

3.系统健壮性与可控性提升

欲望值增长率可通过环境变量灵活调整，便于后续微调主动消息频率。
保留了用户等待回复检测、连续对话等机制，避免重复打扰和对话割裂。

4.反思与改进方向

新机制更贴近真实情侣间的互动节奏，减少了“为话找话”的生硬感。
后续可结合内容分析作为微调补充，进一步提升对话的个性化和温度。
记忆系统、情绪系统等功能可作为后续优化重点，持续提升伴侣体验。
```

- [x] 目前，大多数chatbot都采用一问一答的机制，诚然这样是最符合大多数chatbot对话需求的最优解的，但作为虚拟伴侣应尽量“拟人”，下一步可让模型自主判断当前对话是否需要一次性拆分/发送多条信息

5.3 3:00：提交：
```
为实现更拟人、更自然的对话体验，我们完成了“让模型自主判断当前对话是否需要一次性拆分/发送多条信息”的功能。核心思路和实现过程如下：

1. 需求背景
传统chatbot多采用一问一答的单轮机制，难以模拟真实人类对话中“分段表达、连续输出”的自然节奏。作为虚拟伴侣，机器人需要能够根据内容自主决定将回复拆分为多条消息，提升陪伴感与互动性。

2. 技术方案
结构化输出格式设计
通过在系统提示词中引导模型在需要时输出结构化JSON格式，如：
json
CopyInsert
{
  "messages": [
    {"content": "第一条消息"},
    {"content": "第二条消息"}
  ]
}
支持多种格式（带/不带代码块、以json开头等），兼容主流大模型的输出习惯。
消息解析与拆分处理
新增utils/message_splitter.py模块，负责自动检测和解析模型输出中的结构化JSON片段。解析成功后，机器人会自动将每条消息分开发送，并在消息之间加入类人类的随机延迟（1.5~4秒，随内容长度浮动），避免“信息轰炸”。
流式输出与JSON检测协调
在主对话逻辑（bot.py）中实现了流式输出与结构化消息的智能切换：
检测到回复可能为JSON格式时，先完整收集内容，输出完毕后再整体解析和拆分。
普通文本则继续流式输出，保证响应速度。
健壮性与日志
增强了对多种JSON格式的兼容性，详细记录了解析和分发过程中的每一步日志，便于后续调试和维护。
3. 用户体验优化
每条拆分消息之间的发送间隔根据内容长度动态调整，确保对话节奏自然。
支持模型自主决定是否拆分，适应不同场景下的表达需求（如情感递进、分点叙述、模拟思考等）。
对于非结构化输出，依然保持流畅的单条回复体验。
4. 结果与展望
经过多轮测试，机器人已能在需要时自动将长回复拆分为多条消息发送，显著提升了对话的拟人感和陪伴感。后续可继续优化结构化输出的提示词，引导模型在更丰富的场景下灵活使用该能力。
```

### 主动对话欲望值机制解释

1. 主动对话欲望值系统
每个用户都有一个"主动对话欲望值"（proactive_desire），范围在0到1之间
初始值通常设置为0.2（可通过环境变量配置）
当欲望值超过特定阈值（默认0.7）时，机器人会主动发起对话

2. 欲望值动态调整机制
基于时间的自然增长：随着时间推移，欲望值会自动增长，模拟人类随时间增加的交流欲望
默认每小时增长0.15
增长率会根据用户活跃度动态调整
基于消息内容的调整：
问候语（如"你好"）会增加欲望值(+0.1)
道别语（如"再见"）会大幅减少欲望值(-0.3)
提问会略微增加欲望值(+0.05)
情感表达会增加欲望值(+0.1)
长消息会减少欲望值（超过200字符-0.15，超过100字符-0.05）

3. 智能阈值调整
阈值会根据多种因素动态调整：
时间段：深夜(23:00-7:00)提高阈值(+0.2)，早晚黄金时段(8:00-9:00, 19:00-22:00)降低阈值(-0.1)
用户活跃度：活跃用户阈值略高，不活跃用户阈值略低
上次主动消息时间：如果距离上次主动消息不足2小时，增加阈值(+0.2)
随机因素：10%概率降低阈值(-0.15)，10%概率提高阈值(+0.15)，增加自然性

4. 用户状态感知
如果检测到用户正在等待回复（最后一条是用户消息），会跳过发送主动消息
发送主动消息后会重置欲望值（默认为0.1）

5. 连续对话能力
如果用户没有回复主动消息，系统会在一定时间后（默认2分钟）检查
如果用户仍未回复且未超过最大连续消息数量（默认3条），可能会发送后续消息
后续消息会考虑历史对话上下文，确保对话连贯性
这种机制使机器人的主动交流更加自然、更符合人类对话习惯，能够根据用户的实际交流情况动态调整主动消息的频率，避免了固定时间规划方式可能带来的生硬感。

### 多段输出机制

目前的解决方案是让模型自主根据当前对话需求选择是否需要通过结构化输出json来实现单次对话的多段输出。

提示词的位置：**/utils/message_splitter.py** 的 **def get_structured_message_prompt():** 函数




## 三、语音系统
可尝试介入字节的豆包TTS系统，回复可选文字or语音TTS


## 更完善的控制台机制

## 情绪/心情机制（远期）

## 表情包系统（远期）

