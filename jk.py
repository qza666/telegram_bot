from telegram import ReplyKeyboardMarkup
from telegram.ext import Application, MessageHandler, filters

async def handle_message(update, context):
    keyboard = [['上班', '下班'], ['抽烟', '大厕', '小厕']]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text('请选择你的操作', reply_markup=reply_markup)  # 使用句点代替空文本

def main():
    application = Application.builder().token("7220564243:AAGWg83AZSMEN9kEDDK4lfn-DB2zLDifhr4").build()
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.run_polling()

if __name__ == '__main__':
    main()
