#!/bin/sh
set -eu
rm -rf /home/ChatGPT-Telegram-Bot
# 修改为克隆你的fork仓库，如果你有自己的fork仓库，请替换下面的URL
git clone --recurse-submodules --depth 1 -b main --quiet https://github.com/yym68686/ChatGPT-Telegram-Bot.git
# 覆盖克隆的仓库中的文件，使用你挂载的自定义文件
cp -f /home/bot.py /home/ChatGPT-Telegram-Bot/bot.py
cp -rf /home/utils/* /home/ChatGPT-Telegram-Bot/utils/
# 运行你的自定义版本
python -u /home/ChatGPT-Telegram-Bot/bot.py