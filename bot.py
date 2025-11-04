import os
import logging
from dotenv import load_dotenv
from typing import List

from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
    ConversationHandler,
    MessageHandler,
    filters,
)

# ---------- ЛОГИ ----------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ---------- СТЕЙТЫ ДЛЯ ДИАЛОГА ----------
YEARS, PAID, SALARIES, DAYS = range(4)

# ---------- ХЕЛПЕРЫ ----------
def parse_floats_list(text: str) -> List[float]:
    """
    Принимает строку вида:
    - "60000" -> [60000.0]
    - "60000, 65000, 70000" -> [60000.0, 65000.0, 70000.0]
    Допускает пробелы, запятые, точки.
    """
    parts = [p.strip().replace(" ", "").replace(" ", "").replace("\u00A0", "") for p in text.replace(";", ",").split(",")]
    vals = []
    for p in parts:
        if not p:
            continue
        # Заменяем запятую на точку для десятичных
        p = p.replace(",", ".")
        # Оставляем только цифры и одну точку
        cleaned = []
        dot_seen = False
        for ch in p:
            if ch.isdigit():
                cleaned.append(ch)
            elif ch == "." and not dot_seen:
                cleaned.append(".")
                dot_seen = True
        if not cleaned:
            continue
        try:
            vals.append(float("".join(cleaned)))
        except ValueError:
            pass
    return vals

def calc_vacation_loss(month_salary: float, days: float = 28.0) -> float:
    """
    Упрощённая формула:
    среднедневной = средняя месячная / 29.3
    отпускные за год = среднедневной * дни_отпуска
    """
    return (month_salary / 29.3) * days

def format_rub(amount: float) -> str:
    return f"{amount:,.2f} ₽".replace(",", " ").replace(".00", "")

# ---------- ХЕНДЛЕРЫ ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "Привет! Я помогу посчитать, сколько **потеряно отпускных**, "
        "если вы не были в отпуске и отпускные не выплачивались.\n\n"
        "Нажмите «Начать расчёт»."
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("Начать расчёт", callback_data="start_calc")],
        [InlineKeyboardButton("Помощь", callback_data="help")],
    ])
    if update.message:
        await update.message.reply_text(text, reply_markup=kb, disable_web_page_preview=True)
    else:
        await update.callback_query.message.reply_text(text, reply_markup=kb, disable_web_page_preview=True)

    return ConversationHandler.END

async def help_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.message.reply_text(
        "Как это работает:\n"
        "1) Скажите, сколько лет подряд вы не были в отпуске.\n"
        "2) Скажите, выплачивались ли отпускные за эти годы.\n"
        "3) Если нет — укажите зарплату(ы) и, при необходимости, число дней отпуска (по умолчанию 28).\n"
        "4) Я дам расчёт по каждому году и общий итог."
    )

async def start_calc_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("0", callback_data="years_0"),
            InlineKeyboardButton("1", callback_data="years_1"),
            InlineKeyboardButton("2", callback_data="years_2"),
        ],
        [
            InlineKeyboardButton("3", callback_data="years_3"),
            InlineKeyboardButton("4", callback_data="years_4"),
            InlineKeyboardButton("5+", callback_data="years_5plus"),
        ],
    ])
    await q.message.reply_text(
        "Сколько **лет подряд** вы не были в отпуске?",
        reply_markup=kb
    )
    return YEARS

async def years_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data

    if data == "years_5plus":
        context.user_data["years"] = 5  # возьмём минимум 5; дальше можно будет скорректировать текстом
        await q.message.reply_text(
            "Окей, возьмём **5 лет** (если хотите точнее — пришлите число сообщением). "
            "Теперь ответьте: **выплачивались ли отпускные за эти годы?**",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Да", callback_data="paid_yes"),
                 InlineKeyboardButton("Нет", callback_data="paid_no")]
            ])
        )
        return PAID

    # years_0 .. years_4
    years = int(data.split("_")[1])
    context.user_data["years"] = years

    if years == 0:
        await q.message.reply_text("Если вы были в отпуске или вам всё выплачивали — потерь нет. ✅\n\nНажмите /start для нового расчёта.")
        return ConversationHandler.END

    await q.message.reply_text(
        "Понял. **Выплачивались ли отпускные** за эти годы?",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("Да", callback_data="paid_yes"),
             InlineKeyboardButton("Нет", callback_data="paid_no")]
        ])
    )
    return PAID

async def set_years_from_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Пользователь прислал число лет текстом (после 5+)
    txt = update.message.text.strip()
    try:
        years = int(txt)
        if years < 0:
            raise ValueError
        context.user_data["years"] = years
    except ValueError:
        await update.message.reply_text("Пришлите целое число лет (например, 6).")
        return YEARS

    await update.message.reply_text(
        "Принято. Выплачивались ли отпускные за эти годы?",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("Да", callback_data="paid_yes"),
             InlineKeyboardButton("Нет", callback_data="paid_no")]
        ])
    )
    return PAID

async def paid_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    years = int(context.user_data.get("years", 0))

    if q.data == "paid_yes":
        await q.message.reply_text(
            "Если отпускные **выплачивались**, то потерь нет ✅\n\nНажмите /start, чтобы посчитать для другой ситуации."
        )
        return ConversationHandler.END

    # paid_no -> просим зарплаты
    if years == 1:
        await q.message.reply_text(
            "Пришлите **среднюю месячную зарплату** за этот год (например, 60000)."
        )
    else:
        await q.message.reply_text(
            f"Пришлите **средние месячные зарплаты за каждый из {years} лет**.\n"
            f"Варианты:\n"
            f"• если одинаковая — пришлите одно число (например, 60000)\n"
            f"• если разные — перечислите через запятую (например, 60000, 65000, 70000)"
        )
    return SALARIES

async def salaries_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text
    salaries = parse_floats_list(txt)
    years = int(context.user_data.get("years", 0))

    if not salaries:
        await update.message.reply_text("Не понял сумму(ы). Пришлите число, либо список через запятую (например: 60000 или 60000, 65000).")
        return SALARIES

    if len(salaries) == 1 and years > 1:
        salaries = salaries * years  # повторяем одну и ту же зарплату для всех лет
    elif len(salaries) != years:
        await update.message.reply_text(f"Нужно указать **ровно {years}** значений или одно число для всех лет.")
        return SALARIES

    context.user_data["salaries"] = salaries

    await update.message.reply_text(
        "Сколько дней отпуска положено в год? (По умолчанию **28** — можете прислать число или просто 28)"
    )
    return DAYS

async def days_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text.strip()
    try:
        days = float(txt.replace(",", "."))
        if days <= 0 or days > 56:
            raise ValueError
    except Exception:
        days = 28.0  # если человек прислал что-то странное — берём по умолчанию 28

    context.user_data["days"] = days

    years = int(context.user_data.get("years", 0))
    salaries = context.user_data.get("salaries", [])
    breakdown = []
    total = 0.0

    for i in range(years):
        s = salaries[i]
        loss = calc_vacation_loss(s, days)
        total += loss
        breakdown.append((i + 1, s, loss))

    # Формируем ответ
    lines = [f"Расчёт потерь при **невыплате отпускных** за {years} лет(года):\n"]
    for idx, s, loss in breakdown:
        lines.append(f"Год {idx}: зарплата ~ {format_rub(s)} → невыплаченные отпускные ≈ {format_rub(loss)}")

    lines.append("\nИТОГО потеря за все годы: **" + format_rub(total) + "**")
    lines.append(
        "\nПримечание: расчёт упрощён (среднемесячная ÷ 29.3 × дни). В реальности учёт может отличаться "
        "из-за премий, исключаемых периодов и др."
    )

    await update.message.reply_text("\n".join(lines))
    await update.message.reply_text("Готово ✅\nНажмите /start для нового расчёта.")
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Отменено. Нажмите /start, чтобы начать заново.")
    return ConversationHandler.END

def main():
    load_dotenv()
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("Не найден BOT_TOKEN. Создайте .env с BOT_TOKEN=... или задайте переменную окружения.")

    app = ApplicationBuilder().token(token).build()

    conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            CallbackQueryHandler(start_calc_cb, pattern="^start_calc$"),
            CallbackQueryHandler(help_cb, pattern="^help$"),
        ],
        states={
            YEARS: [
                CallbackQueryHandler(years_cb, pattern="^years_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, set_years_from_text),
            ],
            PAID: [
                CallbackQueryHandler(paid_cb, pattern="^paid_(yes|no)$"),
            ],
            SALARIES: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, salaries_msg),
            ],
            DAYS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, days_msg),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    app.add_handler(conv)
    app.add_handler(CallbackQueryHandler(help_cb, pattern="^help$"))
    app.add_handler(CommandHandler("cancel", cancel))

    logger.info("Bot is starting...")
    app.run_polling()

if __name__ == "__main__":
    main()
