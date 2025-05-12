import os
import asyncio
from pyrogram import Client, filters
from pyrogram.types import Message
from pytgcalls import PyTgCalls, idle
from pytgcalls.types.input_stream import InputStream, InputAudioStream
from pytgcalls import StreamType
import yt_dlp
import openai

import config

# Ensure downloads directory exists
os.makedirs(config.DOWNLOADS_DIR, exist_ok=True)

# Initialize Pyrogram client and PyTgCalls
app = Client(
    "music_chatgpt_bot",
    api_id=config.TELEGRAM_API_ID,
    api_hash=config.TELEGRAM_API_HASH,
    bot_token=config.BOT_TOKEN,
)

pytgcalls = PyTgCalls(app)

openai.api_key = config.OPENAI_API_KEY

# Track currently playing chats and audio files for cleanup
playing_chats = {}

ydl_opts = {
    'format': 'bestaudio/best',
    'quiet': True,
    'no_warnings': True,
    'default_search': 'ytsearch',
    'outtmpl': os.path.join(config.DOWNLOADS_DIR, '%(id)s.%(ext)s'),
    'restrictfilenames': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'source_address': '0.0.0.0',  # Bind to IPv4
    'cachedir': False,
    'postprocessors': [{
        'key': 'FFmpegExtractAudio',
        'preferredcodec': 'mp3',
        'preferredquality': '192',
    }],
}

async def download_audio(query: str) -> str:
    """
    Download audio from YouTube or URL with yt-dlp and save mp3 file.
    Returns the file path or None if failure.
    """
    loop = asyncio.get_event_loop()

    def run_yt_dlp():
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(query, download=True)
            if 'entries' in info:
                info = info['entries'][0]
            filename = ydl.prepare_filename(info)
            # postprocessor changes extension to .mp3
            return filename.rsplit('.', 1)[0] + '.mp3'

    try:
        filepath = await loop.run_in_executor(None, run_yt_dlp)
        return filepath
    except Exception as e:
        print(f"Error downloading audio: {e}")
        return None

async def join_and_play(chat_id: int, audio_file: str):
    """
    Join voice chat of chat_id and start playing audio_file.
    """
    try:
        await pytgcalls.join_group_call(
            chat_id,
            InputStream(InputAudioStream(audio_file)),
            stream_type=StreamType().local_stream,
        )
        playing_chats[chat_id] = audio_file
    except Exception as e:
        print(f"Error joining/playing in voice chat: {e}")
        raise

async def stop_playback(chat_id: int):
    """
    Stop playback and leave voice chat in chat_id.
    Deletes downloaded audio file.
    """
    try:
        await pytgcalls.leave_group_call(chat_id)
        audio_file = playing_chats.pop(chat_id, None)
        if audio_file and os.path.exists(audio_file):
            os.remove(audio_file)
    except Exception as e:
        print(f"Error stopping playback: {e}")

async def pause_playback(chat_id: int):
    """
    Pause playback in voice chat.
    """
    try:
        await pytgcalls.pause_stream(chat_id)
    except Exception as e:
        print(f"Error pausing playback: {e}")

async def resume_playback(chat_id: int):
    """
    Resume playback in voice chat.
    """
    try:
        await pytgcalls.resume_stream(chat_id)
    except Exception as e:
        print(f"Error resuming playback: {e}")

async def chatgpt_response(prompt: str) -> str:
    """
    Get ChatGPT response from OpenAI API.
    """
    try:
        response = openai.Completion.create(
            engine="text-davinci-003",
            prompt=prompt,
            max_tokens=150,
            temperature=0.7,
            n=1,
            stop=None,
        )
        return response.choices[0].text.strip()
    except Exception as e:
        print(f"OpenAI API error: {e}")
        return "Sorry, I couldn't process your request."

# ===== Bot Command Handlers =====

@app.on_message(filters.command("start") & filters.private)
async def start_cmd(_, message: Message):
    await message.reply_text(
        "Hello! I am your AI+Music Telegram Bot.\n\n"
        "Commands:\n"
        "/play <song name or YouTube URL> - Play music in group voice chat\n"
        "/stop - Stop music playback\n"
        "/pause - Pause music\n"
        "/resume - Resume music\n"
        "/chat <your message> - Chat with AI (ChatGPT)\n\n"
        "Add me to a group and start a voice chat to play music!"
    )

@app.on_message(filters.command("play") & filters.group)
async def play_cmd(_, message: Message):
    chat_id = message.chat.id
    if len(message.command) < 2:
        await message.reply_text("Please provide a song name or YouTube URL to play.")
        return
    query = " ".join(message.command[1:])
    await message.reply_text(f"⏳ Searching and downloading: {query}")
    audio_file = await download_audio(query)
    if not audio_file:
        await message.reply_text("Failed to download the audio.")
        return
    try:
        await join_and_play(chat_id, audio_file)
        await message.reply_text(f"▶️ Playing: {query}")
    except Exception as e:
        await message.reply_text(f"Error playing music: {e}")

@app.on_message(filters.command("stop") & filters.group)
async def stop_cmd(_, message: Message):
    chat_id = message.chat.id
    await stop_playback(chat_id)
    await message.reply_text("⏹ Stopped music playback.")

@app.on_message(filters.command("pause") & filters.group)
async def pause_cmd(_, message: Message):
    chat_id = message.chat.id
    await pause_playback(chat_id)
    await message.reply_text("⏸ Music paused.")

@app.on_message(filters.command("resume") & filters.group)
async def resume_cmd(_, message: Message):
    chat_id = message.chat.id
    await resume_playback(chat_id)
    await message.reply_text("▶️ Music resumed.")

@app.on_message(filters.command("chat") & filters.private)
async def chat_cmd(_, message: Message):
    if len(message.command) < 2:
        await message.reply_text("Please provide a message to chat with AI.")
        return
    prompt = " ".join(message.command[1:])
    reply = await chatgpt_response(prompt)
    await message.reply_text(reply)

# ===== Event Handlers =====

@pytgcalls.on_stream_end()
async def on_stream_end(client, update):
    """
    Cleanup after audio stream ends in a chat.
    """
    chat_id = update.chat_id
    audio_file = playing_chats.pop(chat_id, None)
    if audio_file and os.path.exists(audio_file):
        try:
            os.remove(audio_file)
        except Exception as e:
            print(f"Error removing audio file: {e}")

# ===== Main Runner =====

async def run_bot():
    """
    Run the bot program.
    """
    await app.start()
    await pytgcalls.start()
    print("Bot started and running...")
    await idle()
    await app.stop()
