[app]
title           = YTMusic
package.name    = ytmusic
package.domain  = org.ytmusic
version         = 1.1

source.dir      = .
source.include_exts = py,png,jpg,kv,atlas

# ── Android targets ───────────────────────────────────────────────────────────
android.minapi  = 24
android.api     = 33
android.ndk     = 25c

# REMOVED: android.add_src = ffmpeg
#   android.add_src is a Java SOURCE directory flag, not a binary include.
#   Pointing it at an ffmpeg binary folder did nothing useful and could
#   corrupt the APK.  ffmpeg is not needed in this version — yt-dlp uses
#   the Android player client which returns pre-muxed m4a streams.

# ── Permissions ───────────────────────────────────────────────────────────────
android.permissions = INTERNET,FOREGROUND_SERVICE,WAKE_LOCK

# ── Requirements ──────────────────────────────────────────────────────────────
# REMOVED: lxml  — we never import it; html.parser is built-in.
#          lxml needs C compilation which is finicky in p4a and unnecessary.
requirements = \
    python3,\
    kivy==2.3.0,\
    requests,\
    beautifulsoup4,\
    yt_dlp,\
    certifi,\
    urllib3,\
    idna,\
    charset-normalizer

# ── Architecture ──────────────────────────────────────────────────────────────
android.arch    = arm64-v8a
orientation     = portrait

# ── Build settings ────────────────────────────────────────────────────────────
[buildozer]
log_level  = 2
warn_on_root = 1

# REMOVED the ydl_opts = { ... } block that was here.
# buildozer.spec is an INI file — Python dict literals in it are invalid
# and caused the parser to produce wrong build settings.
# yt-dlp options belong in main.py, not in buildozer.spec.
