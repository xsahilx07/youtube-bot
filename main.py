import g4f
from moviepy.video.fx import all as vfx
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
import pickle
import os
import random
import subprocess
from g4f.client import Client
from edge_tts import Communicate
import asyncio
from pypexels import PyPexels
from moviepy.editor import ImageClip, concatenate_videoclips, AudioFileClip, CompositeAudioClip
from PIL import Image, ImageFont, ImageDraw

# --- CONFIGURATION ---
VIDEO_WIDTH = 1920
VIDEO_HEIGHT = 1080

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
# We must explicitly tell g4f to use a provider that doesn't require a key.
client = Client(
    provider=g4f.Provider.You
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
    # Re-raise the error to make the GitHub Action fail properly
    raise e
print("Script Generated successfully.")

# --- 3. GENERATE VOICEOVER ---
print("--- Step 3: Generating Voiceover ---")
async def generate_voiceover():
    communicate = Communicate(script_text, "en-US-JennyNeural")
    await communicate.save("voiceover.mp3")
asyncio.run(generate_voiceover())
print("Voiceover Generated.")

# --- 4. FIND VISUALS ---
print("--- Step 4: Finding Visuals ---")
pexels_api_key = os.environ.get("PEXELS_API_KEY")
if not pexels_api_key:
    print("CRITICAL: Pexels API key not found!")
    raise ValueError("PEXELS_API_KEY secret not set")

pexels_client = PyPexels(api_key=pexels_api_key)

keywords = topic.split()
good_keywords = [kw for kw in keywords if kw.lower() not in ['the', 'of', 'and', 'a', 'in', 'to']]
search_keyword = random.choice(good_keywords) if good_keywords else random.choice(keywords)
print(f"Searching Pexels for: {search_keyword}")

downloaded_clips = []
try:
    search_results = pexels_client.videos.search(query=search_keyword, per_page=15)
    
    for video in search_results.videos:
        video_files = video.video_files
        for vf in video_files:
            if vf.width == 1920 and vf.height == 1080:
                print(f"Downloading clip {vf.id}...")
                subprocess.run(["wget", vf.link, "-O", f"clip_{vf.id}.mp4"], check=True)
                downloaded_clips.append(f"clip_{vf.id}.mp4")
                break
    if not downloaded_clips:
        raise FileNotFoundError("No 1920x1080 videos found.")
except Exception as e:
    print(f"CRITICAL ERROR finding/downloading videos: {e}")
    raise e

# --- 5. CREATE VIDEO ---
print("--- Step 5: Creating Video ---")
try:
    clips = [ImageClip(m).set_duration(7).set_fps(24) for m in downloaded_clips if os.path.exists(m) and os.path.getsize(m) > 0]
    if not clips:
        print("No valid clips to process.")
        exit()
        
    final_clip = concatenate_videoclips(clips, method="compose")
    
    voiceover = AudioFileClip("voiceover.mp3")
    
    # If the video is shorter than the audio, loop the video
    if final_clip.duration < voiceover.duration:
        final_clip = final_clip.fx(vfx.loop, duration=voiceover.duration)
    else: # Otherwise, trim the video to the audio length
        final_clip = final_clip.set_duration(voiceover.duration)

    final_clip.audio = voiceover # Set the main audio
    
    final_clip.write_videofile("final_video.mp4", codec="libx264", audio_codec="aac", temp_audiofile='temp-audio.m4a', remove_temp=True)
    print("Video Created.")
except Exception as e:
    print(f"Error creating video: {e}")
    exit()

# --- 6. CREATE THUMBNAIL ---
print("--- Step 6: Creating Thumbnail ---")
thumbnail_text = topic[:25] + "..." if len(topic) > 25 else topic
try:
    search_results = pexels_client.photos.search(query=search_keyword, per_page=1)
    if search_results.photos:
        photo_url = search_results.photos[0].src.original
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

CLIENT_SECRETS_FILE = "client_secrets.json"
API_NAME = "youtube"
API_VERSION = "v3"
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
TOKEN_PICKLE_FILE = 'token.pickle'

def get_authenticated_service():
    creds = None
    # The file token.pickle stores the user's access and refresh tokens, and is
    # created automatically when the authorization flow completes for the first
    # time.
    if os.path.exists(TOKEN_PICKLE_FILE):
        with open(TOKEN_PICKLE_FILE, 'rb') as token:
            creds = pickle.load(token)
    # If there are no (valid) credentials available, let the user log in.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                print("--- AUTHENTICATION ERROR ---")
                print("Could not refresh token. Need to re-authenticate.")
                flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRETS_FILE, SCOPES)
                # THIS IS THE PART THAT WILL FAIL ON GITHUB ACTIONS
                # IT WILL PROVIDE A URL IN THE LOGS
                creds = flow.run_local_server(port=0)

        else:
            flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRETS_FILE, SCOPES)
            # THIS IS THE PART THAT WILL FAIL ON GITHUB ACTIONS
            # IT WILL PROVIDE A URL IN THE LOGS
            print("--- AUTHENTICATION NEEDED ---")
            print("This will fail and provide a URL. Copy the URL from the logs.")
            creds = flow.run_console() # Use run_console for non-interactive environments

        # Save the credentials for the next run
        with open(TOKEN_PICKLE_FILE, 'wb') as token:
            pickle.dump(creds, token)
    
    return build(API_NAME, API_VERSION, credentials=creds)

youtube = get_authenticated_service()

title = topic
description = script_text[:4500] + "\n\n#history #documentary #automated"

request_body = {
    'snippet': {
        'title': title,
        'description': description,
        'tags': ['history', 'documentary', 'automated', topic.replace(' ', '')],
        'categoryId': '27' # 27 is for Education category
    },
    'status': {
        'privacyStatus': 'private', # IMPORTANT: Upload as private for review
        'selfDeclaredMadeForKids': False,
    }
}

try:
    mediaFile = MediaFileUpload('final_video.mp4', chunksize=-1, resumable=True)
    
    print("Uploading video...")
    response_upload = youtube.videos().insert(
        part='snippet,status',
        body=request_body,
        media_body=mediaFile
    ).execute()

    print("Video uploaded. Now uploading thumbnail...")
    youtube.thumbnails().set(
        videoId=response_upload.get('id'),
        media_body=MediaFileUpload('thumbnail.jpg')
    ).execute()

    print(f"--- SUCCESS: Video '{title}' uploaded with ID: {response_upload.get('id')} ---")

except Exception as e:
    print("--- UPLOAD FAILED OR NEEDS AUTHENTICATION ---")
    print("This is expected on the first run.")
    print("Carefully read the logs above this message for a URL or instructions.")
    print(f"The specific error was: {e}")
    # Re-raise the error to make the GitHub Action fail
    raise e

print("--- SCRIPT FINISHED ---")
