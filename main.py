from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_api_python_client.discovery import build
from google_api_python_client.http import MediaFileUpload
import pickle
import os
import random
import subprocess
from g4f.client import Client
from edge_tts import Communicate
import asyncio
from pexels_api.client import Client as PexelsClient
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
client = Client()
try:
    script_prompt = f"Write a short, 300-word YouTube video script about: {topic}. Start with a hook. Tell a story. End with a call to action to subscribe. Do not use any special formatting or characters."
    script_text = client.chat.completions.create(model="gpt-3.5-turbo", messages=[{"role": "user", "content": script_prompt}]).choices[0].message.content
except Exception as e:
    print(f"Error generating script: {e}")
    exit()
print("Script Generated.")

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
    print("Pexels API key not found!")
    exit()
pexels_client = PexelsClient(pexels_api_key)

keywords = topic.split()
search_keyword = random.choice(keywords)
print(f"Searching Pexels for: {search_keyword}")

try:
    search_videos = pexels_client.videos.search(query=search_keyword, per_page=15)
    video_files_to_download = []
    if search_videos.videos:
        for video in search_videos.videos:
            # Find the highest quality video file that is 1920x1080
            for vf in video.video_files:
                if vf.width == VIDEO_WIDTH and vf.height == VIDEO_HEIGHT:
                    video_files_to_download.append(vf.link)
                    break # Found a suitable file, move to next video
        if not video_files_to_download:
            print("Could not find any 1920x1080 videos.")
            exit()
    else:
        print("No videos found on Pexels for this keyword.")
        exit()
except Exception as e:
    print(f"Error searching Pexels: {e}")
    exit()
    
print(f"Found {len(video_files_to_download)} videos. Downloading...")
downloaded_clips = []
for i, link in enumerate(video_files_to_download):
    try:
        # Use wget to download files as it's common on ubuntu runners
        subprocess.run(["wget", link, "-O", f"clip_{i}.mp4"], check=True)
        downloaded_clips.append(f"clip_{i}.mp4")
    except Exception as e:
        print(f"Could not download clip {i}: {e}")

if not downloaded_clips:
    print("Failed to download any video clips.")
    exit()

# --- 5. CREATE VIDEO ---
print("--- Step 5: Creating Video ---")
clips = [ImageClip(m).set_duration(5).set_fps(25) for m in downloaded_clips if m.endswith('.mp4')]
final_clip = concatenate_videoclips(clips, method="compose")

voiceover = AudioFileClip("voiceover.mp3")
final_clip = final_clip.set_duration(voiceover.duration)

final_audio = CompositeAudioClip([voiceover])
final_clip.audio = final_audio

final_clip.write_videofile("final_video.mp4", codec="libx264", audio_codec="aac")
print("Video Created.")

# --- 6. CREATE THUMBNAIL ---
print("--- Step 6: Creating Thumbnail ---")
thumbnail_text = topic[:20] + "..." if len(topic) > 20 else topic
try:
    search_photos = pexels_client.photos.search(query=search_keyword, per_page=1)
    if search_photos.photos:
        photo_url = search_photos.photos[0].src.original
        subprocess.run(["wget", photo_url, "-O", "thumbnail_bg.jpg"], check=True)
        
        img = Image.open("thumbnail_bg.jpg").resize((1280, 720))
        draw = ImageDraw.Draw(img)
        # You might need to add a font file to your repository for this to look good
        # For now, it will use a default font
        font = ImageFont.load_default(size=70)
        draw.text((50, 550), thumbnail_text.upper(), font=font, fill="yellow", stroke_width=3, stroke_fill="black")
        img.save("thumbnail.jpg")
        print("Thumbnail created.")
    else:
        print("No photo found for thumbnail, skipping.")
except Exception as e:
    print(f"Could not create thumbnail: {e}")


# --- 7. UPLOAD TO YOUTUBE ---
print("--- Step 7: Uploading to YouTube ---")
title = topic
description = script_text[:2000] + "\n\n#history #documentary #automated"

command = [
    "yt-up",
    "final_video.mp4",
    "--title", title,
    "--description", description,
    "--thumbnail", "thumbnail.jpg",
    "--privacy", "private",
    "--client-secrets", "client_secrets.json"
]
try:
    # This will fail the first time and give you a link to authenticate
    print("Running YouTube upload command...")
    subprocess.run(command, check=True)
except Exception as e:
    print("--- UPLOAD FAILED OR NEEDS AUTHENTICATION (This is expected the first time!) ---")
    print("The program will now exit. CHECK THE LOGS CAREFULLY.")
    print("If you see a URL like 'https://accounts.google.com/o/oauth2/...' or a command to run, you must follow those instructions.")
    print("If you see 'yt_up.exceptions.InvalidClientSecretsError', it means your client_secrets.json is wrong.")
    print(f"The specific error was: {e}")

print("--- SCRIPT FINISHED ---")
