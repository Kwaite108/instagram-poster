import os
import time
import json
import cloudinary
import cloudinary.uploader
import cloudinary.api
import anthropic
import requests
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_KEY    = os.getenv("ANTHROPIC_KEY")
ZERNIO_KEY       = os.getenv("ZERNIO_KEY")
ACCOUNT_ID       = os.getenv("ACCOUNT_ID")
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
    api_key=os.getenv("CLOUDINARY_API_KEY"),
    api_secret=os.getenv("CLOUDINARY_API_SECRET")
)

QUEUE_FILE = Path("queue.json")

def load_queue():
    if QUEUE_FILE.exists():
        return json.loads(QUEUE_FILE.read_text())
    return []

def save_queue(queue):
    QUEUE_FILE.write_text(json.dumps(queue, indent=2))

def get_next_photo(skip=[]):
    queue = load_queue()
    posted = {item["filename"] for item in queue if item.get("caption") != "skipped"}
    result = cloudinary.api.resources(
        type="upload",
        prefix="",
        max_results=50
    )
    for resource in result.get("resources", []):
        filename = resource["public_id"]
        if filename not in posted and filename not in skip:
            return {
                "name": filename,
                "url": resource["secure_url"],
                "public_id": resource["public_id"]
            }
    return None

def get_recent_captions():
    try:
        response = requests.get(
            "https://zernio.com/api/v1/posts?limit=5",
            headers={"Authorization": f"Bearer {ZERNIO_KEY}"}
        )
        response.raise_for_status()
        posts = response.json().get("posts", [])
        return [p.get("content", "") for p in posts if p.get("content")]
    except:
        return []

def generate_caption(image_name, feedback=None, original=None):
    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
    recent = get_recent_captions()
    if recent:
        examples = "\n\n---\n\n".join(recent[:3])
        style_block = f"Here are recent captions from our Instagram to match in tone and style:\n\n{examples}\n\n"
    else:
        style_block = """Here are real captions from our Instagram to match in tone and style:

---
Natural light filtered through mature oaks. A winding dirt road. A cabin that feels discovered, not staged. Ideal for premium commercials, narrative films, music videos, and editorial campaigns.
Topanga, California
booking@kellygulch.com
#filmlocation #locationscout #commercialshoot #productiondesign #brandfilm

---
River stone foundation, hand-hewn logs, wrapped deck catching filtered light through mature oaks. An entire story in one frame. 30 minutes from Hollywood.
Topanga, CA
booking@gulchguys.com

---
Inside Kelly Gulch. Exposed log beams, river stone fireplace, natural light pouring through. The space adapts.
Topanga, CA
Booking@gulchguys.com
#kellygulchcabin #filmlocation #cabininterior #topanga #filmproduction

"""

    if feedback and original:
        prompt = f"Rewrite this Instagram caption based on this feedback: {feedback}\n\nOriginal:\n{original}\n\nKeep the same voice and style. Return only the new caption text with no labels or introductions. Use only simple dashes (-) not long dashes."
    else:
        instructions = """Write an Instagram caption in our exact style:
- Short punchy sentences. Fragments are fine.
- Paint a visual picture first, then the pitch.
- Confident, not salesy. Let the location speak.
- Speak directly to filmmakers and productions.
- End with location (Topanga, CA), contact (booking@gulchguys.com), and 4-5 relevant hashtags.
- Use only simple dashes (-) not long dashes.
- Return only the caption text with no labels or introductions."""
        prompt = f"You write Instagram captions for Kelly Gulch Cabin, a historic 1970s log cabin in Topanga, CA, 30 minutes from Hollywood. Premium film location for commercials, narrative films, and music videos. Owners are Kevin and Ben.\n\n{style_block}{instructions}\n\nImage filename: {image_name}"

    response = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=400,
        messages=[{"role": "user", "content": prompt}]
    )
    caption = response.content[0].text.strip()
    # Clean any labels Claude might add
    for label in ["New caption:", "New Caption:", "Here's the caption:", "Caption:"]:
        if caption.startswith(label):
            caption = caption[len(label):].strip()
    return caption

def send_telegram(message, reply_markup=None):
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        json=payload
    )

def send_photo_telegram(url):
    requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto",
        data={"chat_id": TELEGRAM_CHAT_ID, "photo": url}
    )

def get_approval_keyboard():
    return {
        "inline_keyboard": [
            [
                {"text": "YES - Post it", "callback_data": "YES"},
                {"text": "NO - Skip", "callback_data": "NO"}
            ],
            [
                {"text": "Make shorter", "callback_data": "TWEAK:make it shorter"},
                {"text": "More mysterious", "callback_data": "TWEAK:make it more mysterious"}
            ],
            [
                {"text": "More direct", "callback_data": "TWEAK:make it more direct and punchy"},
                {"text": "Write my own", "callback_data": "WRITEOWN"}
            ],
            [
                {"text": "Different photo", "callback_data": "NEXTPHOTO"}
            ]
        ]
    }

def get_telegram_response(timeout_minutes=60):
    print(f"Waiting for your Telegram response...")
    try:
        resp = requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
            params={"timeout": 0, "offset": -1}
        )
        updates = resp.json().get("result", [])
        if updates:
            last_id = updates[-1]["update_id"] + 1
            requests.get(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
                params={"timeout": 0, "offset": last_id}
            )
    except:
        pass

    deadline = time.time() + (timeout_minutes * 60)
    last_update_id = None
    while time.time() < deadline:
        try:
            resp = requests.get(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
                params={"timeout": 30, "offset": last_update_id}
            )
            updates = resp.json().get("result", [])
            for update in updates:
                last_update_id = update["update_id"] + 1
                if "callback_query" in update:
                    callback = update["callback_query"]
                    if str(callback["message"]["chat"]["id"]) == str(TELEGRAM_CHAT_ID):
                        data = callback["data"]
                        requests.post(
                            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/answerCallbackQuery",
                            json={"callback_query_id": callback["id"], "text": "Got it!"}
                        )
                        return data
        except Exception as e:
            print(f"Telegram error: {e}")
        time.sleep(5)
    return None

def get_telegram_text(timeout_minutes=30):
    print(f"Waiting for your typed caption...")
    deadline = time.time() + (timeout_minutes * 60)
    last_update_id = None
    while time.time() < deadline:
        try:
            resp = requests.get(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
                params={"timeout": 30, "offset": last_update_id}
            )
            updates = resp.json().get("result", [])
            for update in updates:
                last_update_id = update["update_id"] + 1
                if "message" in update and "text" in update["message"]:
                    if str(update["message"]["chat"]["id"]) == str(TELEGRAM_CHAT_ID):
                        return update["message"]["text"]
        except Exception as e:
            print(f"Telegram error: {e}")
        time.sleep(5)
    return None

def post_to_instagram(image_url, caption):
    payload = {
        "content": caption,
        "mediaItems": [{"type": "image", "url": image_url}],
        "platforms": [{"platform": "instagram", "accountId": ACCOUNT_ID}],
        "publishNow": True
    }
    result = requests.post(
        "https://zernio.com/api/v1/posts",
        headers={"Authorization": f"Bearer {ZERNIO_KEY}", "Content-Type": "application/json"},
        json=payload
    )
    result.raise_for_status()
    return result.json()

def run_daily():
    print(f"\nKelly Gulch Daily Poster - {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    skipped_this_session = []

    photo = get_next_photo(skip=skipped_this_session)
    if not photo:
        print("No new photos found in Cloudinary.")
        send_telegram("No new photos found. Upload photos to Cloudinary!")
        return

    print(f"Next photo: {photo['name']}")
    caption = generate_caption(photo["name"])
    print("Caption generated. Sending to Telegram...")

    send_photo_telegram(photo["url"])
    send_telegram(
        f"<b>Kelly Gulch - New Post Ready</b>\n\nCaption:\n\n{caption}\n\nWhat would you like to do?",
        get_approval_keyboard()
    )

    current_caption = caption

    while True:
        response = get_telegram_response()

        if not response:
            send_telegram("No response received. Post skipped for today.")
            return

        if response == "YES":
            final_caption = current_caption
            break

        elif response == "NO":
            print("Skipped.")
            send_telegram("Post skipped.")
            return

        elif response.startswith("TWEAK:"):
            feedback = response[6:].strip()
            print(f"Tweaking: {feedback}")
            send_telegram(f"Rewriting: {feedback}...")
            current_caption = generate_caption(photo["name"], feedback=feedback, original=current_caption)
            send_telegram(
                f"Updated caption:\n\n{current_caption}\n\nWhat would you like to do?",
                get_approval_keyboard()
            )

        elif response == "WRITEOWN":
            send_telegram("Type your caption and send it as a message:")
            typed = get_telegram_text()
            if typed:
                current_caption = typed
                send_telegram(
                    f"Your caption:\n\n{current_caption}\n\nPost this?",
                    get_approval_keyboard()
                )
            else:
                send_telegram("No caption received. Skipping.")
                return

        elif response == "NEXTPHOTO":
            skipped_this_session.append(photo["name"])
            photo = get_next_photo(skip=skipped_this_session)
            if not photo:
                send_telegram("No more photos available right now!")
                return
            send_telegram("Getting next photo...")
            caption = generate_caption(photo["name"])
            current_caption = caption
            send_photo_telegram(photo["url"])
            send_telegram(
                f"<b>Kelly Gulch - New Post Ready</b>\n\nCaption:\n\n{caption}\n\nWhat would you like to do?",
                get_approval_keyboard()
            )

        else:
            print(f"Unknown response: {response}")
            return

    print("Posting to Instagram...")
    post_to_instagram(photo["url"], final_caption)

    queue = load_queue()
    queue.append({
        "filename": photo["name"],
        "caption": final_caption,
        "posted_at": datetime.now().isoformat()
    })
    save_queue(queue)
    print("Posted successfully!")
    send_telegram(f"Posted to Instagram!\n\nCaption:\n{final_caption}")

if __name__ == "__main__":
    run_daily()