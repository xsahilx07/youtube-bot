from openai import OpenAI
import os
import random
import subprocess
from edge_tts import Communicate
import asyncio
import requests
from moviepy.editor import VideoFileClip, concatenate_videoclips, AudioFileClip
from moviepy.video.fx import all as vfx
from PIL import Image, ImageFont, ImageDraw
import pickle
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# --- CONFIGURATION ---
VIDEO_WIDTH = 1920
VIDEO_HEIGHT = 1080
TOKEN_PICKLE_FILE = 'token.pickle'
CLIENT_SECRETS_FILE = "client_secrets.json"
API_NAME = "youtube"
API_VERSION = "v3"
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

# --- SETUP: CREATE SECRETS FILES FROM GITHUB SECRETS ---
print("--- Setting up credentials ---")
client_secrets_content = os.environ.get('CLIENT_SECRETS_JSON')
if client_secrets_content:
    with open("client_secrets.json", "w") as f:
        f.write(client_secrets_content)
    print("client_secrets.json created from secret.")
else:
    print("CRITICAL: CLIENT_SECRETS_JSON secret not found!")
    exit()

# --- 1. GET TOPIC ---
print("--- Step 1: Getting Topic ---")
with open("topics.txt", "r+") as f:
    topics = f.readlines()
    if not topics:
        print("No topics left! Exiting.")
        exit()
    topic = topics.pop(0).strip()
    f.seek(0)
    f.truncate()
    f.writelines(topics)
print(f"Topic: {topic}")

# --- 2. GENERATE SCRIPT ---
print("--- Step 2: Generating Script ---")
client = Client(
    provider=g4f.Provider.Grok
)
try:
    script_prompt = f"Write a 300-word YouTube video script about: {topic}. Start with a strong opening hook. Tell a compelling story. Write in simple, clear language. Do not include a title or any special formatting."
    response = client.chat.completions.create(
        messages=[{"role": "user", "content": script_prompt}]
    )
    script_text = response.choices[0].message.content
    if not script_text or len(script_text) < 50:
        print("Generated script is empty or too short. Exiting.")
        raise ValueError("Invalid script generated")
        
except Exception as e:
    print(f"CRITICAL ERROR generating script: {e}")
    raise e
print("Script Generated successfully.")

# --- 3. GENERATE VOICEOVER ---
print("--- Step 3: Generating Voiceover ---")
async def generate_voiceover():
    communicate = Communicate(script_text, "en-US-JennyNeural")
    await communicate.save("voiceover.mp3")
asyncio.run(generate_voiceover())
print("Voiceover Generated.")

# --- 4. FIND VISUALS (MANUAL API CALL) ---
print("--- Step 4: Finding Visuals ---")
pexels_api_key = os.environ.get("PEXELS_API_KEY")
if not pexels_api_key:
    print("CRITICAL: Pexels API key not found!")
    raise ValueError("PEXELS_API_KEY secret not set")

keywords = topic.split()
good_keywords = [kw for kw in keywords if kw.lower() not in ['the', 'of', 'and', 'a', 'in', 'to']]
search_keyword = random.choice(good_keywords) if good_keywords else random.choice(keywords)
print(f"Searching Pexels for: {search_keyword}")

downloaded_clips = []
try:
    headers = {"Authorization": pexels_api_key}
    video_url = f"https://api.pexels.com/videos/search?query={search_keyword}&per_page=15"
    response = requests.get(video_url, headers=headers)
    response.raise_for_status()
    search_results = response.json()

    videos = search_results.get('videos', [])
    if not videos:
        raise FileNotFoundError("Pexels search returned no videos.")
    
    print(f"Found {len(videos)} videos. Downloading...")
    for i, video in enumerate(videos):
        video_files = video.get('video_files', [])
        for vf in video_files:
            if vf.get('width') == 1920 and vf.get('height') == 1080:
                link = vf.get('link')
                print(f"Downloading clip {vf.get('id')}...")
                subprocess.run(["wget", link, "-O", f"clip_{i}.mp4"], check=True)
                downloaded_clips.append(f"clip_{i}.mp4")
                break
    if not downloaded_clips:
        raise FileNotFoundError("No 1920x1080 videos found.")
except Exception as e:
    print(f"CRITICAL ERROR finding/downloading videos: {e}")
    raise e

# --- 5. CREATE VIDEO ---
print("--- Step 5: Creating Video ---")
try:
    clips = [VideoFileClip(m) for m in downloaded_clips if os.path.exists(m) and os.path.getsize(m) > 0]
    if not clips:
        print("CRITICAL: No valid video clips were found after download.")
        raise FileNotFoundError("No valid clips to process.")
    
    processed_clips = [c.set_duration(7).resize(height=VIDEO_HEIGHT) for c in clips]
    final_clip = concatenate_videoclips(processed_clips, method="compose")
    
    voiceover = AudioFileClip("voiceover.mp3")
    
    if final_clip.duration < voiceover.duration:
        final_clip = final_clip.fx(vfx.loop, duration=voiceover.duration)
    else:
        final_clip = final_clip.set_duration(voiceover.duration)

    final_clip.audio = voiceover
    
    print("Writing final video file... This can take a while.")
    final_clip.write_videofile("final_video.mp4", codec="libx264", audio_codec="aac", temp_audiofile='temp-audio.m4a', remove_temp=True, threads=2)
    print("Video Created.")
except Exception as e:
    print(f"CRITICAL ERROR creating video: {e}")
    raise e

# --- 6. CREATE THUMBNAIL (MANUAL API CALL) ---
print("--- Step 6: Creating Thumbnail ---")
thumbnail_text = topic[:25] + "..." if len(topic) > 25 else topic
try:
    headers = {"Authorization": pexels_api_key}
    photo_url_search = f"https://api.pexels.com/v1/search?query={search_keyword}&per_page=1"
    response = requests.get(photo_url_search, headers=headers)
    response.raise_for_status()
    search_results = response.json()
    
    photos = search_results.get('photos', [])
    if photos:
        photo_url = photos[0].get('src', {}).get('original')
        if photo_url:
            subprocess.run(["wget", photo_url, "-O", "thumbnail_bg.jpg"], check=True)
            
            img = Image.open("thumbnail_bg.jpg").resize((1280, 720))
            draw = ImageDraw.Draw(img)
            font = ImageFont.load_default(size=70)
            draw.text((50, 550), thumbnail_text.upper(), font=font, fill="yellow", stroke_width=3, stroke_fill="black")
            img.save("thumbnail.jpg")
            print("Thumbnail created.")
    else:
        print("No photo found for thumbnail, skipping.")
except Exception as e:
    print(f"Warning: Could not create thumbnail: {e}")

# --- 7. UPLOAD TO YOUTUBE ---
print("--- Step 7: Uploading to YouTube ---")
def get_authenticated_service():
    creds = None
    if not os.path.exists(TOKEN_PICKLE_FILE):
         raise FileNotFoundError("CRITICAL: token.pickle not found. Please authenticate locally and upload the file.")

    with open(TOKEN_PICKLE_FILE, 'rb') as token:
        creds = pickle.load(token)
    
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print("Refreshing expired token...")
            creds.refresh(Request())
            with open(TOKEN_PICKLE_FILE, 'wb') as token:
                pickle.dump(creds, token)
        else:
            raise Exception("Invalid or expired token. Please re-authenticate locally and upload a new token.pickle.")
    
    return build(API_NAME, API_VERSION, credentials=creds)

youtube = get_authenticated_service()

title = topic
description = script_text[:4500] + "\n\n#history #documentary #automated"
request_body = {
    'snippet': {
        'title': title, 'description': description, 'tags': ['history', 'documentary', 'automated', topic.replace(' ', '')], 'categoryId': '27'
    }, 'status': {'privacyStatus': 'private', 'selfDeclaredMadeForKids': False,}
}
try:
    mediaFile = MediaFileUpload('final_video.mp4', chunksize=-1, resumable=True)
    print("Uploading video...")
    response_upload = youtube.videos().insert(part='snippet,status', body=request_body, media_body=mediaFile).execute()
    print("Video uploaded. Now uploading thumbnail...")
    if os.path.exists('thumbnail.jpg'):
        youtube.thumbnails().set(videoId=response_upload.get('id'), media_body=MediaFileUpload('thumbnail.jpg')).execute()
        print("Thumbnail uploaded.")
    else:
        print("Thumbnail file not found, skipping upload.")
    print(f"--- SUCCESS: Video '{title}' uploaded with ID: {response_upload.get('id')} ---")
except Exception as e:
    print(f"CRITICAL ERROR during upload: {e}")
    raise e
