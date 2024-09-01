import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ConversationHandler, ContextTypes
from datetime import datetime, time
import pytz
import re
import sqlite3
import json

# 自定义 JSON 编码器
class TimeEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, time):
            return obj.strftime('%H:%M')
        return super().default(obj)

# 自定义 JSON 解码器
def time_decoder(dct):
    for key, value in dct.items():
        if key in ['work_start', 'work_end'] and isinstance(value, str):
            try:
                dct[key] = datetime.strptime(value, '%H:%M').time()
            except ValueError:
                pass  # 如果转换失败，保持原样
    return dct

# 创建数据库连接并执行SQL
def execute_db(query, params=(), fetch='one'):
    conn = sqlite3.connect('settings.db')
    cursor = conn.cursor()
    cursor.execute(query, params)
    if fetch == 'one':
        result = cursor.fetchone()
    elif fetch == 'all':
        result = cursor.fetchall()
    else:
        result = None
    conn.commit()
    conn.close()
    return result if result is not None else []

# 创建表格
def create_table():
    execute_db('''
        CREATE TABLE IF NOT EXISTS exempt_list (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER,
            username TEXT
        )
    ''')
    execute_db('''
        CREATE TABLE IF NOT EXISTS group_settings (
            chat_id INTEGER PRIMARY KEY,
            admin_id INTEGER,
            settings TEXT
        )
    ''')

# 从数据库加载设置
def load_settings():
    global group_settings
    results = execute_db('SELECT chat_id, admin_id, settings FROM group_settings', fetch='all')
    for chat_id, admin_id, settings_json in results:
        settings = json.loads(settings_json, object_hook=time_decoder)
        group_settings[chat_id] = {'admin': admin_id, **settings}

# 保存设置到数据库
def save_settings(chat_id):
    admin_id = group_settings[chat_id]['admin']
    settings = {k: v for k, v in group_settings[chat_id].items() if k != 'admin'}
    settings_json = json.dumps(settings, cls=TimeEncoder)
    execute_db('INSERT OR REPLACE INTO group_settings (chat_id, admin_id, settings) VALUES (?, ?, ?)',
               (chat_id, admin_id, settings_json))


# 启用日志
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# 定义会话状态
ADMIN, SETTING, WORK_START, WORK_END, BIG_TOILET, BIG_TOILET_TIME, SMALL_TOILET, SMALL_TOILET_TIME, SMOKE, SMOKE_TIME, EXEMPT_LIST = range(11)

MODIFY_CHOICE, MODIFY_VALUE = range(2)

# 存储群组设置
group_settings = {}

# 时间解析
def parse_time(time_str):
    if time_str == "24:00":
        return time(hour=0, minute=0)
    try:
        return datetime.strptime(time_str, "%H:%M").time()
    except ValueError:
        raise ValueError("时间格式无效。请使用 HH:MM（例如 08:00 或 24:00）")

# 检查是否是管理员或白名单用户
def is_authorized(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    username = update.effective_user.username
    is_admin = group_settings.get(chat_id, {}).get('admin') == user_id
    is_whitelisted = execute_db('SELECT 1 FROM exempt_list WHERE chat_id = ? AND username = ?', (chat_id, username), fetch=True)
    return is_admin or is_whitelisted

# 通用的设置处理函数
async def handle_setting(update: Update, context: ContextTypes.DEFAULT_TYPE, setting_key, next_state, prompt):
    try:
        value = int(update.message.text) if setting_key in ['big_toilet', 'big_toilet_time', 'small_toilet', 'small_toilet_time', 'smoke', 'smoke_time'] else parse_time(update.message.text)
        chat_id = update.effective_chat.id
        group_settings[chat_id][setting_key] = value
        save_settings(chat_id)
        await update.message.reply_text(prompt)
        return next_state
    except ValueError as e:
        await update.message.reply_text(str(e))
        return next_state - 1

# 处理 "管理员" 命令
async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    
    if chat_id not in group_settings:
        group_settings[chat_id] = {'admin': user_id}
        save_settings(chat_id)
        beijing_time = datetime.now(pytz.timezone('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M:%S')
        await update.message.reply_text(f"初始化成功，当前时间为北京时间 {beijing_time}，如果需要其他时区，请联系@Mrfacai\n\n\n发送\"设置\"触发继续配置")
    elif is_authorized(update, context):
        await update.message.reply_text("你已经是管理员了。发送\"设置\"开始配置。")
    else:
        await update.message.reply_text("你不是管理员")
    return ConversationHandler.END

# 处理 "设置" 命令
async def settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_authorized(update, context):
        await update.message.reply_text("进入设置流程。请输入上班时间：\n(格式：HH:MM，如 08:00)")
        return WORK_START
    else:
        await update.message.reply_text("你没有权限进行设置")
        return ConversationHandler.END

# 添加免打卡用户到数据库
def add_exempt_user(chat_id, username):
    execute_db('INSERT OR REPLACE INTO exempt_list (chat_id, username) VALUES (?, ?)', (chat_id, username))
    logging.info(f"Added user {username} to exempt list for chat {chat_id}")

# 处理免打卡名单输入
async def set_exempt_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    usernames = re.findall(r'@(\w+)', update.message.text)
    if usernames:
        for username in usernames:
            add_exempt_user(chat_id, username)
        await update.message.reply_text(f"数据已经写入数据库。添加的用户：{', '.join(usernames)}")
        logging.info(f"Set exempt list for chat {chat_id}: {usernames}")  # 添加日志
        return ConversationHandler.END
    else:
        await update.message.reply_text("没有检测到有效的用户名，请重新输入。")
        return EXEMPT_LIST

# 查看免打卡名单
async def view_exempt_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_authorized(update, context):
        chat_id = update.effective_chat.id
        results = execute_db('SELECT DISTINCT username FROM exempt_list WHERE chat_id = ?', (chat_id,), fetch='all')
        usernames = [f"@{result[0]}" for result in results]
        if usernames:
            await update.message.reply_text("免打卡名单：\n" + "\n".join(usernames))
        else:
            await update.message.reply_text("当前没有免打卡名单。")
        logging.info(f"Viewed exempt list for chat {chat_id}: {usernames}")  # 添加日志
    else:
        await update.message.reply_text("你没有权限查看白名单")

# 更新查看信息并显示工作设置和白名单
async def view_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_authorized(update, context):
        chat_id = update.effective_chat.id
        settings = group_settings.get(chat_id, {})
        
        # 从数据库中获取白名单
        whitelist_users = execute_db('SELECT DISTINCT username FROM exempt_list WHERE chat_id = ?', (chat_id,), fetch='all')
        whitelist_text = "\n".join([f"@{user[0]}" for user in whitelist_users]) if whitelist_users else "无"

        # 格式化时间设置
        work_start = settings.get('work_start', '未设置')
        work_end = settings.get('work_end', '未设置')
        if isinstance(work_start, time):
            work_start = work_start.strftime('%H:%M')
        if isinstance(work_end, time):
            work_end = work_end.strftime('%H:%M')

        await update.message.reply_text(
            f"当前设置:\n上班时间: {work_start}\n下班时间: {work_end}\n"
            f"大厕次数: {settings.get('big_toilet', '未设置')}\n大厕时间: {settings.get('big_toilet_time', '未设置')}分钟\n"
            f"小厕次数: {settings.get('small_toilet', '未设置')}\n小厕时间: {settings.get('small_toilet_time', '未设置')}分钟\n"
            f"抽烟次数: {settings.get('smoke', '未设置')}\n抽烟时间: {settings.get('smoke_time', '未设置')}分钟\n\n"
            f"白名单:\n{whitelist_text}\n\n发送\"查看\"可随时查看清单"
        )
        logging.info(f"Viewed info for chat {chat_id}")  # 添加日志
    else:
        await update.message.reply_text("你没有权限查看信息")

# 处理修改请求
async def modify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_authorized(update, context):
        await update.message.reply_text(
            "请选择要修改的项目:\n"
            "1. 上班时间\n"
            "2. 下班时间\n"
            "3. 大厕次数\n"
            "4. 大厕时间\n"
            "5. 小厕次数\n"
            "6. 小厕时间\n"
            "7. 抽烟次数\n"
            "8. 抽烟时间\n"
            "9. 添加白名单\n"
            "请输入对应的数字。"
        )
        return MODIFY_CHOICE
    else:
        await update.message.reply_text("你没有权限进行修改")
        return ConversationHandler.END
    
# 处理修改选择
async def handle_modify_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    choice = update.message.text
    choices = {
        "1": ("work_start", "请输入新的上班时间 (HH:MM):"),
        "2": ("work_end", "请输入新的下班时间 (HH:MM):"),
        "3": ("big_toilet", "请输入新的大厕次数:"),
        "4": ("big_toilet_time", "请输入新的大厕时间 (分钟):"),
        "5": ("small_toilet", "请输入新的小厕次数:"),
        "6": ("small_toilet_time", "请输入新的小厕时间 (分钟):"),
        "7": ("smoke", "请输入新的抽烟次数:"),
        "8": ("smoke_time", "请输入新的抽烟时间 (分钟):"),
        "9": ("exempt_list", "请输入要添加的白名单用户 (@username):"),
    }
    
    if choice in choices:
        context.user_data['modify_item'] = choices[choice][0]
        await update.message.reply_text(choices[choice][1])
        return MODIFY_VALUE
    else:
        await update.message.reply_text("无效的选择，请重新输入。")
        return MODIFY_CHOICE

# 处理修改值
async def handle_modify_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    item = context.user_data['modify_item']
    value = update.message.text

    if item == "exempt_list":
        usernames = re.findall(r'@(\w+)', value)
        if usernames:
            for username in usernames:
                add_exempt_user(chat_id, username)
            await update.message.reply_text("白名单已更新。")
        else:
            await update.message.reply_text("没有检测到有效的用户名，请重新输入。")
            return MODIFY_VALUE
    else:
        try:
            if item in ['work_start', 'work_end']:
                value = parse_time(value)
            elif item in ['big_toilet', 'big_toilet_time', 'small_toilet', 'small_toilet_time', 'smoke', 'smoke_time']:
                value = int(value)
            
            if chat_id not in group_settings:
                group_settings[chat_id] = {}
            group_settings[chat_id][item] = value
            save_settings(chat_id)
            await update.message.reply_text(f"{item} 已更新为 {value}")
        except ValueError as e:
            await update.message.reply_text(f"输入错误: {str(e)}")
            return MODIFY_VALUE

    return ConversationHandler.END



def main():
    logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
    
    create_table()  # 初始化数据库表
    load_settings()  # 加载设置

    # 在这里替换为你的Bot Token
    application = Application.builder().token("7220564243:AAGWg83AZSMEN9kEDDK4lfn-DB2zLDifhr4").build()

    conv_handler = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex('^管理员$'), admin),
            MessageHandler(filters.Regex('^设置$'), settings),
            MessageHandler(filters.Regex('^查看$'), view_info),
            MessageHandler(filters.Regex('^白名单$'), view_exempt_list),
            MessageHandler(filters.Regex('^修改$'), modify)
        ],
        states={
            WORK_START: [MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: handle_setting(u, c, 'work_start', WORK_END, "请输入下班时间：\n(格式：HH:MM，如 18:00 或 24:00)"))],
            WORK_END: [MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: handle_setting(u, c, 'work_end', BIG_TOILET, "请输入大厕次数：\n(-1为不限制)"))],
            BIG_TOILET: [MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: handle_setting(u, c, 'big_toilet', BIG_TOILET_TIME, "请输入大厕时间：\n(单位分钟)"))],
            BIG_TOILET_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: handle_setting(u, c, 'big_toilet_time', SMALL_TOILET, "请输入小厕次数：\n(-1为不限制)"))],
            SMALL_TOILET: [MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: handle_setting(u, c, 'small_toilet', SMALL_TOILET_TIME, "请输入小厕时间：\n(单位分钟)"))],
            SMALL_TOILET_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: handle_setting(u, c, 'small_toilet_time', SMOKE, "请输入抽烟次数：\n(-1为不限制)"))],
            SMOKE: [MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: handle_setting(u, c, 'smoke', SMOKE_TIME, "请输入抽烟时间：\n(单位分钟)"))],
            SMOKE_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: handle_setting(u, c, 'smoke_time', EXEMPT_LIST, "请输入免打卡名单（格式：@xxxx @xxxx）："))],
            EXEMPT_LIST: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_exempt_list)],
            MODIFY_CHOICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_modify_choice)],
            MODIFY_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_modify_value)],
        },
        fallbacks=[
            MessageHandler(filters.Regex('^管理员$'), admin),
            MessageHandler(filters.Regex('^设置$'), settings),
            MessageHandler(filters.Regex('^查看$'), view_info),
            MessageHandler(filters.Regex('^白名单$'), view_exempt_list),
            MessageHandler(filters.Regex('^修改$'), modify)
        ]
    )

    application.add_handler(conv_handler)
    application.run_polling()

if __name__ == "__main__":
    main()