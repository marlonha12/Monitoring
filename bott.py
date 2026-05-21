import asyncio
import logging
from datetime import datetime
import os
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

import pyowm
from pyowm.utils import config as owm_config
from pyowm.commons.exceptions import NotFoundError, UnauthorizedError

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
OWM_API_KEY = os.getenv("OWM_API_KEY")

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# Инициализация бота и диспетчера
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# 1. Получаем дефолтный словарь конфигурации pyowm
config_dict = owm_config.get_default_config()

# 2. Устанавливаем русский язык
config_dict['language'] = 'ru'

# 3. Передаем измененный конфиг в именованный параметр `config`
owm = pyowm.OWM(OWM_API_KEY, config=config_dict)
mgr = owm.weather_manager()

# Клавиатура главного меню
main_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🌤 Погода сейчас")],
        [KeyboardButton(text="📅 Прогноз на 5 дней")],
        [KeyboardButton(text="⚙️ Настройки уведомлений")]
    ],
    resize_keyboard=True
)

# Клавиатура для настроек
settings_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🕐 Установить интервал"), KeyboardButton(text="🔕 Отключить уведомления")],
        [KeyboardButton(text="⬅️ Назад в меню")]
    ],
    resize_keyboard=True
)

# Состояния для FSM
class WeatherStates(StatesGroup):
    waiting_for_city = State()
    waiting_for_interval = State()
    waiting_for_forecast_city = State()

# Хранилище пользовательских настроек
user_settings = {}

def get_weather_text(city_name: str) -> str:
    """Получение и форматирование текущей погоды"""
    try:
        observation = mgr.weather_at_place(city_name)
        weather = observation.weather
        
        temp = weather.temperature('celsius')['temp']
        feels_like = weather.temperature('celsius')['feels_like']
        humidity = weather.humidity
        wind = weather.wind()['speed']
        status = weather.detailed_status  # Текст на русском
        pressure = weather.pressure['press']
        
        weather_emoji = {
            'clear': '☀️',
            'clouds': '☁️',
            'rain': '🌧',
            'snow': '❄️',
            'thunderstorm': '⛈',
            'mist': '🌫',
            'fog': '🌫'
        }.get(weather.status.lower(), '🌡')
        
        return (
            f"{weather_emoji} Погода в {city_name.title()}:\n\n"
            f"🌡 Температура: {temp:.1f}°C (ощущается как {feels_like:.1f}°C)\n"
            f"💧 Влажность: {humidity}%\n"
            f"💨 Ветер: {wind:.1f} м/с\n"
            f"📊 Давление: {pressure} гПа\n"
            f"📝 {status.capitalize()}\n\n"
            f"🕐 Обновлено: {datetime.now().strftime('%H:%M:%S')}"
        )
    except NotFoundError:
        return f"❌ Город '{city_name}' не найден. Проверьте название."
    except UnauthorizedError:
        return "❌ Ошибка API погоды. Проверьте ключ."
    except Exception as e:
        return f"❌ Ошибка: {str(e)}"

def get_forecast_text(city_name: str) -> str:
    """Получение прогноза на 5 дней"""
    try:
        forecast = mgr.forecast_at_place(city_name, '3h', limit=40)
        result = f"📅 Прогноз погоды для {city_name.title()} на 5 дней:\n\n"
        
        last_date = None
        count = 0
        
        for weather in forecast.forecast:
            date = datetime.fromtimestamp(weather.reference_time())
            if date.hour in [12, 15] and date.date() != last_date:
                if count >= 5:
                    break
                    
                temp = weather.temperature('celsius')['temp']
                status = weather.detailed_status
                
                main_status = weather.status.lower()
                if main_status == 'clear':
                    emoji = "☀️"
                elif main_status == 'clouds':
                    emoji = "☁️"
                elif main_status == 'rain':
                    emoji = "🌧"
                elif main_status == 'snow':
                    emoji = "❄️"
                else:
                    emoji = "🌡"
                
                result += f"📅 {date.strftime('%d.%m.%Y')}:\n"
                result += f"   {emoji} {temp:.1f}°C | {status.capitalize()}\n\n"
                
                last_date = date.date()
                count += 1
                
        return result
    except NotFoundError:
        return f"❌ Город '{city_name}' не найден."
    except Exception as e:
        return f"❌ Ошибка: {str(e)}"

async def send_weather_notification(user_id: int, city: str):
    """Отправка уведомления о погоде"""
    weather_text = get_weather_text(city)
    if not weather_text.startswith("❌"):
        await bot.send_message(user_id, f"🔔 Погодное уведомление:\n\n{weather_text}")

async def check_and_notify():
    """Фоновая задача для проверки и отправки уведомлений"""
    while True:
        await asyncio.sleep(60)
        current_time = datetime.now().timestamp()
        
        for user_id, settings in user_settings.items():
            if "interval" in settings and settings["interval"] > 0:
                last_notified = settings.get("last_notified", 0)
                
                if current_time - last_notified >= settings["interval"]:
                    if "city" in settings:
                        await send_weather_notification(user_id, settings["city"])
                        settings["last_notified"] = current_time

@dp.message(Command("start"))
async def cmd_start(message: Message):
    welcome_text = (
        "🌍 Привет! Я бот для мониторинга погоды!\n\n"
        "Я могу:\n"
        "✅ Показать текущую погоду в любом городе\n"
        "✅ Предоставить прогноз на 5 дней\n"
        "✅ Настроить автоматические уведомления\n\n"
        "Используй кнопки ниже для навигации 👇"
    )
    await message.answer(welcome_text, reply_markup=main_keyboard)

@dp.message(lambda msg: msg.text == "🌤 Погода сейчас")
async def weather_now(message: Message, state: FSMContext):
    await state.set_state(WeatherStates.waiting_for_city)
    await message.answer("🏙 Введите название города:", reply_markup=types.ReplyKeyboardRemove())

@dp.message(lambda msg: msg.text == "📅 Прогноз на 5 дней")
async def forecast_5days(message: Message, state: FSMContext):
    await state.set_state(WeatherStates.waiting_for_forecast_city)
    await message.answer("🏙 Введите город для прогноза:", reply_markup=types.ReplyKeyboardRemove())

@dp.message(WeatherStates.waiting_for_city)
async def process_city(message: Message, state: FSMContext):
    city = message.text.strip()
    weather_text = get_weather_text(city)
    await message.answer(weather_text, reply_markup=main_keyboard)
    
    user_id = message.from_user.id
    if user_id not in user_settings:
        user_settings[user_id] = {}
    user_settings[user_id]["city"] = city
    await state.clear()

@dp.message(WeatherStates.waiting_for_forecast_city)
async def process_forecast_city(message: Message, state: FSMContext):
    city = message.text.strip()
    forecast_text = get_forecast_text(city)
    await message.answer(forecast_text, reply_markup=main_keyboard)
    await state.clear()

@dp.message(lambda msg: msg.text == "⚙️ Настройки уведомлений")
async def settings_menu(message: Message):
    settings = user_settings.get(message.from_user.id, {})
    interval = settings.get("interval", 0)
    interval_text = "🔕 Выключены" if interval == 0 else f"🕐 {interval // 3600} час(а/ов)"
    city = settings.get("city", "не задан")
    
    status_text = (
        f"⚙️ Настройки уведомлений:\n\n"
        f"📍 Город: {city}\n"
        f"⏰ Интервал: {interval_text}\n\n"
        f"Выберите действие:"
    )
    await message.answer(status_text, reply_markup=settings_keyboard)

@dp.message(lambda msg: msg.text == "🕐 Установить интервал")
async def set_interval(message: Message, state: FSMContext):
    user_id = message.from_user.id
    if user_id not in user_settings or "city" not in user_settings[user_id]:
        await message.answer(
            "❌ Сначала узнайте погоду в вашем городе, нажав '🌤 Погода сейчас'",
            reply_markup=main_keyboard
        )
        return
    
    await state.set_state(WeatherStates.waiting_for_interval)
    await message.answer(
        "🕐 Введите интервал уведомлений в часах (от 1 до 24):\n"
        "Например: 3 (уведомления каждые 3 часа)\n"
        "Или 0 чтобы отключить",
        reply_markup=types.ReplyKeyboardRemove()
    )

@dp.message(WeatherStates.waiting_for_interval)
async def process_interval(message: Message, state: FSMContext):
    try:
        hours = int(message.text.strip())
        user_id = message.from_user.id
        
        if hours == 0:
            user_settings[user_id]["interval"] = 0
            user_settings[user_id].pop("last_notified", None)
            await message.answer("🔕 Уведомления отключены.", reply_markup=settings_keyboard)
        elif 1 <= hours <= 24:
            user_settings[user_id]["interval"] = hours * 3600
            user_settings[user_id]["last_notified"] = datetime.now().timestamp()
            city = user_settings[user_id].get("city", "вашем городе")
            
            await message.answer(
                f"✅ Интервал уведомлений установлен на {hours} час(а/ов).\n"
                f"Я буду присылать погоду в {city} каждые {hours} часа(ов).",
                reply_markup=settings_keyboard
            )
        else:
            await message.answer("❌ Введите число от 1 до 24 (или 0).", reply_markup=settings_keyboard)
    except ValueError:
        await message.answer("❌ Пожалуйста, введите целое число.", reply_markup=settings_keyboard)
    await state.clear()

@dp.message(lambda msg: msg.text == "🔕 Отключить уведомления")
async def disable_notifications(message: Message):
    user_id = message.from_user.id
    if user_id in user_settings:
        user_settings[user_id]["interval"] = 0
        user_settings[user_id].pop("last_notified", None)
    await message.answer("🔕 Уведомления отключены.", reply_markup=settings_keyboard)

@dp.message(lambda msg: msg.text == "⬅️ Назад в меню")
async def back_to_menu(message: Message):
    await message.answer("Главное меню:", reply_markup=main_keyboard)

async def main():
    asyncio.create_task(check_and_notify())
    print("🤖 Бот запущен и готов к работе!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
