import os
import uuid

UPLOAD_DIR = "app/uploads"


async def save_audio_file(audio):

    os.makedirs(UPLOAD_DIR, exist_ok=True)

    # Never trust the client's filename: it can contain "../" and every browser
    # recording is called "recorded.webm", so concurrent uploads would collide.
    extension = os.path.splitext(audio.filename or "")[1].lower()
    if not extension.isascii() or not extension[1:].isalnum():
        extension = ""
    file_path = os.path.join(UPLOAD_DIR, f"{uuid.uuid4()}{extension}")

    with open(file_path, "wb") as buffer:
        content = await audio.read()
        buffer.write(content)

    return file_path
