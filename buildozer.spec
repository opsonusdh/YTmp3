[app]

# ─── App identity ────────────────────────────────────────
title = YTMusic
package.name = ytmusic
package.domain = org.ytmusic
version = 1.0

source.dir = .
source.include_exts = py,png,jpg,kv,atlas

# ─── Entry point ─────────────────────────────────────────
# main.py is default

# ─── Android targets ─────────────────────────────────────
android.minapi = 24
android.api = 33
android.ndk = 25b
android.add_src = ffmpeg

# ─── Permissions (only what actually works) ──────────────
android.permissions = INTERNET,FOREGROUND_SERVICE,WAKE_LOCK

# ─── Python requirements ─────────────────────────────────
requirements = python3==3.10.11,kivy==2.3.0,requests,beautifulsoup4,lxml,yt_dlp,certifi,urllib3,idna,charset-normalizer

# ─── Architecture ────────────────────────────────────────
android.arch = arm64-v8a

# ─── Orientation ─────────────────────────────────────────
orientation = portrait

# ─── Build settings ──────────────────────────────────────
[buildozer]
log_level = 2
warn_on_root = 1

ydl_opts = {
    'quiet': True,
    'skip_download': True,
    'noplaylist': True,
    'format': 'bestaudio[ext=m4a]/bestaudio',
    'extractor_args': {
        'youtube': {
            'player_client': ['android']
        }
    },
    'ignoreerrors': True,
}
