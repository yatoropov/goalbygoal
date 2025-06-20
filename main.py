import os
import logging
import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.types import ReplyKeyboardMarkup, Update
from aiogram.utils.executor import start_webhook
from aiohttp import web
from google.cloud import firestore

from PIL import Image
from io import BytesIO
from datetime import datetime
import pytz

logging.basicConfig(level=logging.INFO)

TOKEN = os.getenv("TELEGRAM_TOKEN")
WEBHOOK_PATH = '/webhook'
WEBHOOK_URL = os.getenv('WEBHOOK_URL', '') + WEBHOOK_PATH

bot = Bot(token=TOKEN)
dp = Dispatcher(bot)

# --- Firestore (default)
db = firestore.Client(project=os.getenv('GOOGLE_CLOUD_PROJECT'), database="(default)")

PARENT_MENU = (
    ReplyKeyboardMarkup(resize_keyboard=True)
    .add("Додати задачу")
    .add("Історія")
    .add("Додати дитину")
    .add("Видалити дитину")
)
CHILD_MENU = ReplyKeyboardMarkup(resize_keyboard=True).add("Мої задачі")
TASK_LIST = ["Застелити ліжко", "Помити чашку", "Почистити зуби"]

# Runtime state for simple flows
parent_states = {}
child_states = {}

def gen_invite_code():
    import random, string
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))

# --- Firestore async wrappers

def save_user_sync(user_id, data):
    db.collection('users').document(str(user_id)).set(data)

async def save_user(user_id, data):
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, save_user_sync, user_id, data)

def get_user_sync(user_id):
    doc = db.collection('users').document(str(user_id)).get()
    return doc.to_dict() if doc.exists else None

async def get_user(user_id):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, get_user_sync, user_id)

def save_invite_sync(code, parent_id):
    db.collection('invites').document(code).set({'parent_id': parent_id})

async def save_invite(code, parent_id):
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, save_invite_sync, code, parent_id)

def get_invite_sync(code):
    doc = db.collection('invites').document(code).get()
    return doc.to_dict() if doc.exists else None

async def get_invite(code):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, get_invite_sync, code)

def add_child_to_parent_sync(parent_id, child_id):
    parent = get_user_sync(parent_id)
    if parent:
        children = parent.get('children', [])
        if child_id not in children:
            children.append(child_id)
            db.collection('users').document(str(parent_id)).update({'children': children})

async def add_child_to_parent(parent_id, child_id):
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, add_child_to_parent_sync, parent_id, child_id)

def remove_child_from_parent_sync(parent_id, child_id):
    parent = get_user_sync(parent_id)
    if parent:
        children = parent.get('children', [])
        if child_id in children:
            children.remove(child_id)
            db.collection('users').document(str(parent_id)).update({'children': children})

async def remove_child_from_parent(parent_id, child_id):
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, remove_child_from_parent_sync, parent_id, child_id)

def update_user_sync(user_id, data):
    db.collection('users').document(str(user_id)).update(data)

async def update_user(user_id, data):
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, update_user_sync, user_id, data)

# --- EXIF analysis for photo
async def is_photo_from_today(file_bytes, timezone='Europe/Kyiv'):
    """Check if a photo was taken today.

    Parameters
    ----------
    file_bytes : bytes
        Raw bytes of the image file.
    timezone : str, optional
        Timezone used to interpret the EXIF timestamp. Defaults to ``'Europe/Kyiv'``.

    Returns
    -------
    tuple[bool, str]
        ``True`` and an informational message if the photo is from today,
        otherwise ``False`` and a note describing the issue.
    """
    try:
        img = Image.open(BytesIO(file_bytes))
        exif_data = img.getexif()
        date_time_original = exif_data.get(36867) or exif_data.get(306)  # 36867: DateTimeOriginal, 306: DateTime
        if not date_time_original:
            return False, "EXIF-дані не містять дати зйомки."
        try:
            photo_dt = datetime.strptime(date_time_original, "%Y:%m:%d %H:%M:%S")
            photo_dt = pytz.timezone(timezone).localize(photo_dt)
        except Exception as e:
            return False, f"Не вдалося розпізнати дату: {e}"

        now = datetime.now(pytz.timezone(timezone)).date()
        is_today = photo_dt.date() == now

        return is_today, f"Фото зроблено: {photo_dt.strftime('%Y-%m-%d %H:%M:%S')}"
    except Exception as e:
        return False, f"Не вдалося відкрити фото: {e}"

# --- Хендлери

@dp.message_handler(commands=['start'])
async def start_cmd(message: types.Message):
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("Я батько/мати", "Я дитина")
    await message.answer("👋 Вас вітає GoalByGoal!\n\nОбери свою роль:", reply_markup=kb)

@dp.message_handler(lambda m: m.text == "Я батько/мати")
async def parent_register(message: types.Message):
    code = gen_invite_code()
    await save_user(message.from_user.id, {'role': 'parent', 'invite': code, 'children': [], 'tasks': {}})
    await save_invite(code, message.from_user.id)
    await message.answer(
        f"👨‍👩‍👧‍👦 Ви — батько/мати у GoalByGoal!\n\n"
        f"Ваш інвайт-код для підключення дитини: {code}\n"
        f"Передайте цей код дитині у Telegram або будь-яким зручним способом.\n\n"
        "Далі оберіть дію ⬇️", reply_markup=PARENT_MENU
    )

@dp.message_handler(lambda m: m.text == "Я дитина")
async def child_register(message: types.Message):
    await message.answer(
        "Введіть інвайт-код від батьків, щоб приєднатись до сімейної команди GoalByGoal! (6 символів):"
    )

@dp.message_handler(lambda m: m.text and m.text.isalnum() and len(m.text) == 6)
async def process_invite(message: types.Message):
    code = message.text.upper()
    invite = await get_invite(code)
    if invite:
        parent_id = invite.get('parent_id')
        parent = await get_user(parent_id)
        if not parent or parent.get('role') != 'parent':
            await message.answer("Помилка: інвайт-код неактуальний або батько не створений.")
            return
        await save_user(message.from_user.id, {"role": "child", "parent": parent_id, "tasks": {}})
        await add_child_to_parent(parent_id, message.from_user.id)
        child_states[message.from_user.id] = "awaiting_name"
        await message.answer("🎉 Ви успішно приєдналися до GoalByGoal! Введіть своє ім'я:")
        await bot.send_message(parent_id, "👦👧 Дитина приєдналася до вашої сімʼї в GoalByGoal. Очікуємо її ім'я.", reply_markup=PARENT_MENU)
    else:
        await message.answer("Інвайт-код невірний. Спробуйте ще раз:")

@dp.message_handler(lambda m: child_states.get(m.from_user.id) == "awaiting_name")
async def set_child_name(message: types.Message):
    await update_user(message.from_user.id, {"name": message.text})
    state_parent = (await get_user(message.from_user.id)).get("parent")
    child_states.pop(message.from_user.id, None)
    await message.answer("Ім'я збережено!", reply_markup=CHILD_MENU)
    if state_parent:
        await bot.send_message(state_parent, f"Дитина тепер має ім'я: {message.text}", reply_markup=PARENT_MENU)

@dp.message_handler(lambda m: m.text == "Додати задачу")
async def add_task(message: types.Message):
    user = await get_user(message.from_user.id)
    if user and user.get("role") == "parent":
        children = user.get("children", [])
        if not children:
            await message.answer("У вас немає підключених дітей.", reply_markup=PARENT_MENU)
            return
        if len(children) == 1:
            parent_states[message.from_user.id] = {"action": "assign_task", "child_id": children[0]}
            kb = ReplyKeyboardMarkup(resize_keyboard=True)
            for t in TASK_LIST:
                kb.add(t)
            await message.answer("Оберіть задачу:", reply_markup=kb)
        else:
            kb = ReplyKeyboardMarkup(resize_keyboard=True)
            mapping = {}
            for child_id in children:
                child = await get_user(child_id)
                name = child.get("name") or str(child_id)[-6:]
                label = f"{name} ({str(child_id)[-6:]})"
                mapping[label] = child_id
                kb.add(label)
            parent_states[message.from_user.id] = {"action": "select_child", "map": mapping}
            await message.answer("Оберіть дитину:", reply_markup=kb)

@dp.message_handler(lambda m: m.text == "Додати дитину")
async def parent_add_child(message: types.Message):
    user = await get_user(message.from_user.id)
    if user and user.get("role") == "parent":
        code = gen_invite_code()
        await save_invite(code, message.from_user.id)
        await update_user(message.from_user.id, {"invite": code})
        await message.answer(
            f"Новий інвайт-код для дитини: {code}", reply_markup=PARENT_MENU
        )

@dp.message_handler(lambda m: m.text == "Видалити дитину")
async def parent_remove_child(message: types.Message):
    user = await get_user(message.from_user.id)
    if user and user.get("role") == "parent":
        children = user.get("children", [])
        if not children:
            await message.answer("У вас немає підключених дітей.", reply_markup=PARENT_MENU)
            return
        kb = ReplyKeyboardMarkup(resize_keyboard=True)
        mapping = {}
        for child_id in children:
            child = await get_user(child_id)
            name = child.get("name") or str(child_id)[-6:]
            label = f"{name} ({str(child_id)[-6:]})"
            mapping[label] = child_id
            kb.add(label)
        parent_states[message.from_user.id] = {"action": "remove_child", "map": mapping}
        await message.answer("Оберіть дитину для видалення:", reply_markup=kb)

@dp.message_handler(lambda m: parent_states.get(m.from_user.id, {}).get("action") == "remove_child")
async def confirm_remove_child(message: types.Message):
    state = parent_states.get(message.from_user.id, {})
    child_id = state.get("map", {}).get(message.text)
    if child_id:
        await remove_child_from_parent(message.from_user.id, child_id)
        await update_user(child_id, {"parent": None})
        parent_states.pop(message.from_user.id, None)
        await message.answer("Дитину видалено.", reply_markup=PARENT_MENU)
        await bot.send_message(child_id, "Вас видалили з сім'ї GoalByGoal.")
    else:
        await message.answer("Оберіть дитину зі списку")

@dp.message_handler(lambda m: parent_states.get(m.from_user.id, {}).get("action") == "select_child")
async def choose_child(message: types.Message):
    state = parent_states.get(message.from_user.id, {})
    child_id = state.get("map", {}).get(message.text)
    if child_id:
        kb = ReplyKeyboardMarkup(resize_keyboard=True)
        for t in TASK_LIST:
            kb.add(t)
        parent_states[message.from_user.id] = {"action": "assign_task", "child_id": child_id}
        await message.answer("Оберіть задачу:", reply_markup=kb)
    else:
        await message.answer("Оберіть дитину зі списку")

@dp.message_handler(lambda m: m.text in TASK_LIST)
async def select_task(message: types.Message):
    user = await get_user(message.from_user.id)
    if user and user.get('role') == 'parent':
        tasks = user.get('tasks', {})
        tasks[message.text] = {'reward': 20, 'active': True}
        await update_user(message.from_user.id, {'tasks': tasks})
        state = parent_states.pop(message.from_user.id, None)
        if state and state.get('action') == 'assign_task':
            children = [state.get('child_id')]
        else:
            children = user.get('children', [])
        for child_id in children:
            child = await get_user(child_id)
            child_tasks = child.get('tasks', {}) if child else {}
            child_tasks[message.text] = {'status': 'active'}
            await update_user(child_id, {'tasks': child_tasks})
            await bot.send_message(child_id, f"🆕 Нова задача в GoalByGoal: {message.text}. Надішліть фото виконання.")
        await message.answer(f"Задача '{message.text}' додана та активована!", reply_markup=PARENT_MENU)

@dp.message_handler(content_types=types.ContentType.PHOTO)
async def handle_photo(message: types.Message):
    user = await get_user(message.from_user.id)
    if user and user.get('role') == 'child':
        photo = message.photo[-1]
        file = await bot.get_file(photo.file_id)
        file_bytes = await bot.download_file(file.file_path)
        file_bytes = file_bytes.read()

        is_today, info = await is_photo_from_today(file_bytes)

        if is_today:
            reply_text = f"✅ Молодець! Фото зроблено сьогодні ({info}). Завдання зараховано!"
        else:
            reply_text = f"⚠️ Фото зроблено НЕ сьогодні! {info}\nБудь ласка, надішли свіже фото."

        await message.answer(reply_text)

        parent_id = user['parent']
        await bot.send_message(
            parent_id,
            f"Дитина відправила фото виконання задачі у GoalByGoal.\n\nРезультат перевірки: {reply_text}",
            reply_markup=PARENT_MENU
        )

@dp.message_handler(lambda m: m.text == "Історія")
async def history(message: types.Message):
    user = await get_user(message.from_user.id)
    if user and user.get('role') == 'parent':
        text = "🗂 Історія задач у GoalByGoal:\n"
        for t, info in user.get('tasks', {}).items():
            text += f"{t}: {'активна' if info['active'] else 'неактивна'}\n"
        await message.answer(text, reply_markup=PARENT_MENU)

@dp.message_handler(lambda m: m.text == "Мої задачі")
async def my_tasks(message: types.Message):
    user = await get_user(message.from_user.id)
    if user and user.get('role') == 'child':
        text = "📋 Ваші задачі у GoalByGoal:\n"
        for t, info in user.get('tasks', {}).items():
            text += f"{t}: {info['status']}\n"
        await message.answer(text, reply_markup=CHILD_MENU)

# --- Webhook events

async def on_startup(dp):
    await bot.set_webhook(WEBHOOK_URL)
    logging.info(f"Webhook set to {WEBHOOK_URL}")


async def webhook_handler(request):
    try:
        update = Update(**(await request.json()))
        await dp.process_update(update)
    except Exception as e:
        logging.error(f"Exception in webhook_handler: {e}", exc_info=True)
    return web.Response()

async def health_check(request):
    return web.Response(text="OK")

if __name__ == "__main__":
    app = web.Application()
    app.router.add_get('/health', health_check)
    app.router.add_post(WEBHOOK_PATH, webhook_handler)
    start_webhook(
        dispatcher=dp,
        webhook_path=WEBHOOK_PATH,
        on_startup=on_startup,
        skip_updates=True,
        host="0.0.0.0",
        port=int(os.environ.get('PORT', 8080)),
    )
