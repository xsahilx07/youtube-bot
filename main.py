from openai import OpenAI
import os
import random
import subprocess
from edge_tts import Communicate
import asyncio
import requests
from moviepy.editor import VideoFileClip, concatenate_videoclips, AudioFileClip, TextClip, CompositeVideoClip, ImageClip
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

# --- 1. GET SCRIPT AND TOPIC FROM FILE ---
print("--- Step 1: Getting Script and Topic ---")
try:
    with open("script.txt", "r+", encoding="utf-8") as f:
        lines = f.readlines()
        if not lines:
            print("script.txt is empty! Exiting.")
            exit()
        
        # The first line is the topic
        topic = lines[0].strip()
        # The rest of the lines are the script
        script_text = "".join(lines[1:])
        
        if not topic or not script_text:
            raise ValueError("script.txt is missing a topic on the first line or the script body.")

        # We don't remove the script, you will do that manually
except FileNotFoundError:
    print("CRITICAL: script.txt not found! Please create it and add a topic and script.")
    raise
except Exception as e:
    print(f"CRITICAL ERROR reading script.txt: {e}")
    raise e

print(f"Topic: {topic}")
print("Script Loaded successfully.")

# --- # --- 3. GENERATE VOICEOVER & SUBTITLES ---
print("--- Step 3: Generating Voiceover & Subtitles ---")
async def generate_voice_and_subs():
    communicate = Communicate(script_text, "en-US-JennyNeural")
    
    # The new way: Manually build the SRT file while streaming audio
    srt_content = ""
    index = 1
    start_time = 0
    with open("voiceover.mp3", "wb") as audio_file:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_file.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                end_time = start_time + (chunk["duration"] / 1000)
                
                # Format times for SRT
                start_h, start_rem = divmod(start_time, 3600)
                start_m, start_s = divmod(start_rem, 60)
                start_ms = int((start_s - int(start_s)) * 1000)
                
                end_h, end_rem = divmod(end_time, 3600)
                end_m, end_s = divmod(end_rem, 60)
                end_ms = int((end_s - int(end_s)) * 1000)

                srt_content += f"{index}\n"
                srt_content += f"{int(start_h):02}:{int(start_m):02}:{int(start_s):02},{start_ms:03} --> {int(end_h):02}:{int(end_m):02}:{int(end_s):02},{end_ms:03}\n"
                srt_content += f"{chunk['text']}\n\n"
                
                index += 1
                start_time = end_time

    with open("voiceover.srt", "w", encoding="utf-8") as srt_file:
        srt_file.write(srt_content)

asyncio.run(generate_voice_and_subs())
print("Voiceover & Subtitles Generated.")

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

# --- 5. CREATE "VIRAL STYLE" VIDEO ---
print("--- Step 5: Creating Viral Style Video ---")
try:
    # Load the audio and get its duration
    voiceover = AudioFileClip("voiceover.mp3")
    video_duration = voiceover.duration

    # --- Prepare Background Clips ---
    # The key is to have MANY short clips. Let's aim for a clip every 3 seconds.
    num_clips_needed = int(video_duration / 3) + 1
    
    # We will reuse our downloaded clips to meet this demand
    clips_to_use = []
    if downloaded_clips:
        for i in range(num_clips_needed):
            # Loop through the downloaded clips
            clips_to_use.append(downloaded_clips[i % len(downloaded_clips)])

    print(f"Video requires {num_clips_needed} short clips. Reusing downloaded assets.")
    
    # Create VideoFileClip objects for each 3-second segment
    video_segments = []
    for clip_path in clips_to_use:
        if os.path.exists(clip_path) and os.path.getsize(clip_path) > 0:
            clip = VideoFileClip(clip_path).set_fps(24).resize(width=VIDEO_WIDTH)
            # If the original clip is shorter than 3s, loop it. Otherwise, take a 3s chunk.
            if clip.duration < 3:
                segment = clip.fx(vfx.loop, duration=3)
            else:
                start_time = random.uniform(0, clip.duration - 3)
                segment = clip.subclip(start_time, start_time + 3)
            
            video_segments.append(segment.set_duration(3))
    
    if not video_segments:
        raise FileNotFoundError("Could not process any video segments.")

    # --- Concatenate for the final background video ---
    background_video = concatenate_videoclips(video_segments).set_duration(video_duration)

    # --- Create High-Impact Subtitle Clips ---
    # This function defines the style of the text
    def subtitle_generator(txt):
        # Split text into words to potentially highlight one later (too complex for now)
        return TextClip(
            txt.upper(),  # Make text uppercase for impact
            font='Arial-Bold',
            fontsize=90,  # Larger font size
            color='white',
            stroke_color='black',
            stroke_width=3,
            method='caption', # Helps with word wrapping
            size=(VIDEO_WIDTH*0.8, None) # Text shouldn't span the whole screen
        )
    
    subtitles = SubtitlesClip("voiceover.srt", subtitle_generator)
    # Position subtitles in the center of the screen
    subtitles = subtitles.set_position('center')

    # --- Combine everything ---
    print("Compositing final video...")
    final_video = CompositeVideoClip([background_video, subtitles], size=(VIDEO_WIDTH, VIDEO_HEIGHT))
    final_video = final_video.set_audio(voiceover)
    final_video = final_video.set_duration(video_duration)

    print("Writing final video file...")
    final_video.write_videofile("final_video.mp4", codec="libx264", audio_codec="aac", temp_audiofile='temp-audio.m4a', remove_temp=True, threads=2)
    print("Viral Style Video Created.")

except Exception as e:
    print(f"CRITICAL ERROR creating viral video: {e}")
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
