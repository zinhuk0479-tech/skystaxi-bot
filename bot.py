import asyncio
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
import aiosqlite
import os
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
DRIVERS_CHAT_ID = os.getenv("DRIVERS_CHAT_ID")

bot = Bot(token=TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

logging.basicConfig(level=logging.INFO)

user_phones = {}

class OrderStates(StatesGroup):
    waiting_pickup = State()
    waiting_destination = State()
    waiting_price = State()

async def init_db():
    async with aiosqlite.connect('orders.db') as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                phone TEXT,
                pickup TEXT,
                destination TEXT,
                client_price TEXT,
                status TEXT DEFAULT 'new',
                driver_id INTEGER
            )
        ''')
        await db.commit()

def main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚕 Новый заказ", callback_data="call_taxi")]
    ])

@dp.message(Command("start"))
async def start(message: types.Message):
    if message.from_user.id not in user_phones:
        kb = ReplyKeyboardMarkup(keyboard=[
            [KeyboardButton(text="📱 Поделиться номером", request_contact=True)]
        ], resize_keyboard=True, one_time_keyboard=True)
        await message.answer("👋 Добро пожаловать!\nДля продолжения поделитесь номером телефона.", reply_markup=kb)
    else:
        await message.answer("👋 Добро пожаловать обратно!", reply_markup=main_menu())

@dp.message()
async def handle_message(message: types.Message, state: FSMContext):
    if message.contact:
        phone = message.contact.phone_number
        user_phones[message.from_user.id] = phone
        await message.answer(f"✅ Номер {phone} сохранён!", reply_markup=ReplyKeyboardRemove())
        await message.answer("Теперь можете вызвать такси.", reply_markup=main_menu())
        return

    # Если состояние активное — обрабатываем
    current_state = await state.get_state()
    if current_state:
        if current_state == OrderStates.waiting_pickup:
            await pickup(message, state)
        elif current_state == OrderStates.waiting_destination:
            await destination(message, state)
        elif current_state == OrderStates.waiting_price:
            await price(message, state)
    else:
        if "новый заказ" in message.text.lower():
            await call_taxi_handler(message)
        else:
            await message.answer("Нажмите кнопку «Новый заказ».")

async def call_taxi_handler(message):
    kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="📍 Поделиться геопозицией", request_location=True)]], resize_keyboard=True, one_time_keyboard=True)
    await message.answer("📍 Откуда забрать? (геопозиция или текст)", reply_markup=kb)

@dp.callback_query(lambda c: c.data == "call_taxi")
async def call_taxi(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await call_taxi_handler(callback.message)

@dp.message(OrderStates.waiting_pickup)
async def pickup(message: types.Message, state: FSMContext):
    if message.location:
        loc = f"📍 {message.location.latitude:.5f}, {message.location.longitude:.5f}"
        await state.update_data(pickup=loc)
    else:
        await state.update_data(pickup=message.text)
    await message.answer("✅ Сохранено", reply_markup=types.ReplyKeyboardRemove())
    await message.answer("🏁 Куда ехать? (геопозиция или текст)")
    await state.set_state(OrderStates.waiting_destination)

@dp.message(OrderStates.waiting_destination)
async def destination(message: types.Message, state: FSMContext):
    if message.location:
        loc = f"📍 {message.location.latitude:.5f}, {message.location.longitude:.5f}"
        await state.update_data(destination=loc)
    else:
        await state.update_data(destination=message.text)
    await message.answer("✅ Сохранено", reply_markup=types.ReplyKeyboardRemove())
    await message.answer("💰 Цена? (или /skip)")
    await state.set_state(OrderStates.waiting_price)

@dp.message(OrderStates.waiting_price)
async def price(message: types.Message, state: FSMContext):
    data = await state.get_data()
    price = message.text if message.text != "/skip" else "Договорная"
    phone = user_phones.get(message.from_user.id, 'Не указан')
    
    async with aiosqlite.connect('orders.db') as db:
        cursor = await db.execute(
            "INSERT INTO orders (user_id, phone, pickup, destination, client_price) VALUES (?, ?, ?, ?, ?)",
            (message.from_user.id, phone, data.get('pickup'), data.get('destination'), price)
        )
        order_id = cursor.lastrowid
        await db.commit()
    
    order_text = f"""🆕 Новый заказ #{order_id}

📍 От: {data.get('pickup', 'Не указано')}
🏁 Куда: {data.get('destination', 'Не указано')}
💰 Цена: {price}
👤 Клиент: @{message.from_user.username}
📱 Телефон: {phone}"""

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Принять", callback_data=f"accept_{order_id}")],
        [InlineKeyboardButton(text="❌ Отказать", callback_data=f"reject_{order_id}")]
    ])
    
    await bot.send_message(DRIVERS_CHAT_ID, order_text, reply_markup=kb)
    await message.answer("✅ Заказ отправлен!", reply_markup=main_menu())
    await state.clear()

@dp.callback_query(lambda c: c.data.startswith("accept_"))
async def accept_order(callback: types.CallbackQuery):
    order_id = int(callback.data.split("_")[1])
    driver_name = callback.from_user.full_name
    async with aiosqlite.connect('orders.db') as db:
        await db.execute("UPDATE orders SET status='accepted', driver_id=? WHERE id=?", (callback.from_user.id, order_id))
        await db.commit()
    async with aiosqlite.connect('orders.db') as db:
        row = await db.execute("SELECT user_id FROM orders WHERE id=?", (order_id,))
        result = await row.fetchone()
        if result:
            client_id = result[0]
            await bot.send_message(client_id, f"🎉 Заказ #{order_id} принят водителем **{driver_name}**!", parse_mode="HTML")
    await callback.message.edit_text(callback.message.text + f"\n\n✅ Принят: {driver_name}")
    await callback.answer("✅ Принято!", show_alert=True)

@dp.callback_query(lambda c: c.data.startswith("reject_"))
async def reject_order(callback: types.CallbackQuery):
    order_id = int(callback.data.split("_")[1])
    async with aiosqlite.connect('orders.db') as db:
        row = await db.execute("SELECT user_id FROM orders WHERE id=?", (order_id,))
        result = await row.fetchone()
        if result:
            client_id = result[0]
            await bot.send_message(client_id, f"❌ Заказ #{order_id} отклонён.\nОтказано из-за низкой цены.\nСоздайте новый заказ с лучшей ценой!", reply_markup=main_menu())
    await callback.message.edit_text(callback.message.text + "\n\n❌ Отклонён")
    await callback.answer("❌ Отказ принят")

async def main():
    await init_db()
    print("🚀 Бот запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())