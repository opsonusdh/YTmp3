#!/usr/bin/env python3
"""
YTMusicPlayer — Background YouTube music player for Android
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  GUI    : Kivy  (UI + audio — single framework, no pygame)
  Search : requests + BeautifulSoup4 (scrapes ytInitialData)
  Audio  : yt-dlp  (30-second buffer → play → full DL → swap)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

# ─────────────────────────────────────────────
#  STDLIB
# ─────────────────────────────────────────────
import json
import os
import re
import sys
import tempfile
import threading
import time
import uuid

# ─────────────────────────────────────────────
#  THIRD-PARTY
# ─────────────────────────────────────────────
import requests
from bs4 import BeautifulSoup
import yt_dlp

# ─────────────────────────────────────────────
#  KIVY — configure BEFORE any other kivy import
# ─────────────────────────────────────────────
from kivy.config import Config
Config.set('kivy', 'keyboard_mode', 'systemanddock')  # Android keyboard

from kivy.app import App
from kivy.clock import Clock
from kivy.core.audio import SoundLoader
from kivy.core.window import Window
from kivy.graphics import Color, Rectangle, Line
from kivy.metrics import dp, sp
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.image import AsyncImage
from kivy.uix.label import Label
from kivy.uix.progressbar import ProgressBar
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput
from kivy.uix.widget import Widget
from kivy.utils import platform


# ═══════════════════════════════════════════════
#  CONFIG & CONSTANTS
# ═══════════════════════════════════════════════
BUFFER_SECS = 30

# Cache dir — Android-safe
CACHE_DIR = tempfile.gettempdir()
if platform == 'android':
    try:
        from android.storage import app_storage_path
        CACHE_DIR = app_storage_path()
    except Exception:
        pass

def get_ffmpeg_path():
    if platform == "android":
        from android.storage import app_storage_path
        return os.path.join(app_storage_path(), "ffmpeg")
    return "ffmpeg"

def ensure_exec(path):
    try:
        os.chmod(path, 0o755)
    except:
        pass

ffmpeg_path = get_ffmpeg_path()
ensure_exec(ffmpeg_path)



# Pretend to be a mobile Chrome browser — avoids YouTube bot detection
YT_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/120.0.6099.115 Mobile Safari/537.36'
    ),
    'Accept-Language': 'en-US,en;q=0.9',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
}

# ─────────────────────────────────────────────
#  PALETTE  (dark AMOLED — saves battery on Android)
# ─────────────────────────────────────────────
C_BG        = (0.05, 0.05, 0.07, 1)    # near-black background
C_SURFACE   = (0.11, 0.11, 0.14, 1)    # card / input surface
C_ELEVATED  = (0.16, 0.16, 0.20, 1)    # raised surface (player bar)
C_ACCENT    = (0.92, 0.18, 0.22, 1)    # YouTube red
C_ACCENT_DIM= (0.55, 0.10, 0.13, 1)    # dimmed accent
C_WHITE     = (1.00, 1.00, 1.00, 1)
C_GREY      = (0.55, 0.55, 0.62, 1)
C_DIVIDER   = (0.20, 0.20, 0.24, 1)
C_SUCCESS   = (0.15, 0.78, 0.46, 1)
C_WARNING   = (0.95, 0.61, 0.07, 1)

def _format_duration(seconds):
    if not seconds:
        return "--:--"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _format_views(views):
    if not views:
        return ""
    if views >= 1_000_000:
        return f"{views/1_000_000:.1f}M views"
    if views >= 1_000:
        return f"{views/1_000:.1f}K views"
    return f"{views} views"
# ═══════════════════════════════════════════════
#  YOUTUBE SEARCHER
# ═══════════════════════════════════════════════
class SilentLogger:
    def debug(self, msg): pass
    def warning(self, msg): print("[yt-dlp WARNING]", msg)
    def error(self, msg): print("[yt-dlp ERROR]", msg)
    
class YouTubeSearcher:
    """
    Scrapes YouTube search results without any API key.
    YouTube injects all result data as JSON into <script>ytInitialData={...}</script>.
    BeautifulSoup4 finds that script tag; we parse the JSON ourselves.
    """
    SEARCH_URL = "https://www.youtube.com/results?search_query={}"
    WATCH_URL  = "https://www.youtube.com/watch?v={}"
    THUMB_URL  = "https://img.youtube.com/vi/{}/mqdefault.jpg"

    
    def search(self, query: str) -> list:
        results = []

        ydl_opts = {
            'quiet': True,
            'skip_download': True,
            'noplaylist': True,
            'ignoreerrors': True,
            'logger': SilentLogger(),
            'ffmpeg_location': ffmpeg_path,
            'format': 'bestaudio[ext=m4a]/bestaudio',
        }
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(f"ytsearch10:{query}", download=False)

            if not info:
                return []

            entries = info.get('entries') or []

            for entry in entries:
                if not entry:
                    continue

                vid = entry.get('id')
                if not vid:
                    continue

                duration = entry.get('duration')
                view_count = entry.get('view_count')

                results.append({
                    'id': vid,
                    'url': entry.get('webpage_url') or f"https://www.youtube.com/watch?v={vid}",
                    'title': entry.get('title') or 'Unknown',
                    'channel': entry.get('uploader') or '',
                    'duration': _format_duration(duration),
                    'views': _format_views(view_count),
                    'thumb': entry.get('thumbnail') or f"https://img.youtube.com/vi/{vid}/mqdefault.jpg",
                })

                return results
        except Exception as e:
            print(f"[SEARCHER] {e}")
    
    def _parse_initial_data(self, soup: BeautifulSoup) -> list:
        """
        YouTube embeds results as:
          var ytInitialData = {...};
        inside a <script> tag.  We grab that JSON and walk the tree.
        """
        results = []

        for tag in soup.find_all('script'):
            src = tag.string or ''
            if 'ytInitialData' not in src:
                continue

            # Extract the JSON object
            match = re.search(
                r'var ytInitialData\s*=\s*(\{.+?\});\s*(?:</script>|var\s)',
                src, re.DOTALL
            )
            if not match:
                # Fallback — some pages have no trailing semicolon guard
                match = re.search(r'ytInitialData\s*=\s*(\{.+)', src, re.DOTALL)
                if match:
                    raw = match.group(1).rstrip(';')
                    # Trim to valid JSON by counting braces
                    raw = self._balanced_json(raw)
                else:
                    continue

            raw = match.group(1) if match else raw
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue

            try:
                sections = (
                    data['contents']
                        ['twoColumnSearchResultsRenderer']
                        ['primaryContents']
                        ['sectionListRenderer']
                        ['contents']
                )
            except (KeyError, TypeError):
                continue

            for section in sections:
                items = section.get('itemSectionRenderer', {}).get('contents', [])
                for item in items:
                    vr = item.get('videoRenderer')
                    if not vr:
                        continue
                    vid = vr.get('videoId', '').strip()
                    if not vid:
                        continue

                    title   = self._text(vr, 'title')
                    channel = self._text(vr, 'ownerText')
                    dur     = vr.get('lengthText', {}).get('simpleText', '--:--')
                    views   = vr.get('viewCountText', {}).get('simpleText', '')

                    results.append({
                        'id':      vid,
                        'url':     self.WATCH_URL.format(vid),
                        'title':   title or 'Unknown',
                        'channel': channel,
                        'duration':dur,
                        'views':   views,
                        'thumb':   self.THUMB_URL.format(vid),
                    })
            break   # found the right script tag

        return results[:25]

    @staticmethod
    def _text(renderer: dict, key: str) -> str:
        try:
            return renderer[key]['runs'][0]['text']
        except (KeyError, IndexError, TypeError):
            return ''

    @staticmethod
    def _balanced_json(s: str) -> str:
        depth, i = 0, 0
        for i, ch in enumerate(s):
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    return s[:i+1]
        return s


# ═══════════════════════════════════════════════
#  AUDIO PLAYER
# ═══════════════════════════════════════════════
class AudioPlayer:
    """
    Manages a play queue and handles buffered playback:

    Pipeline for each track
    ────────────────────────
    1. Spawn Thread A → yt-dlp downloads first BUFFER_SECS seconds
       (uses --download-sections; requires ffmpeg for keyframe cuts)
    2. Thread A done  → kivy SoundLoader plays the buffer file
    3. Thread B (parallel) → yt-dlp downloads full track
    4. When buffer finishes playing → swap to full file at same position
       If full not ready yet → wait up to 60 s, then advance queue

    Fallback (no ffmpeg / download-sections fails)
    ────────────────────────────────────────────
    yt-dlp extracts the direct CDN stream URL; SoundLoader streams it.
    Android's MediaPlayer handles HTTP streams natively.
    """

    # ── States ─────────────────────────────────
    STATE_IDLE    = 'idle'
    STATE_LOADING = 'loading'
    STATE_PLAYING = 'playing'
    STATE_PAUSED  = 'paused'
    STATE_STOPPED = 'stopped'
    STATE_ERROR   = 'error'

    def __init__(self):
        # Callbacks (set by UI layer)
        self.on_state    = None   # fn(state: str, track: dict | None)
        self.on_progress = None   # fn(pos: float, length: float)
        self.on_error    = None   # fn(msg: str)

        self._queue      = []
        self._idx        = -1
        self._sound      = None
        self._tmps       = []            # temp file paths → cleaned up on stop
        self._stop_ev    = threading.Event()
        self._full_ready = threading.Event()
        self._full_path  = None
        self._prog_ev    = None
        self._in_swap    = False         # prevents double-swap

    # ── Public API ────────────────────────────

    def play_queue(self, tracks: list, start: int = 0):
        """Replace queue and start playing from index `start`."""
        self._hard_stop()
        self._queue = list(tracks)
        self._idx   = max(0, min(start, len(tracks) - 1))
        self._begin()

    def play_index(self, idx: int):
        """Jump to a specific queue index."""
        if 0 <= idx < len(self._queue):
            self._hard_stop(keep_queue=True)
            self._idx = idx
            self._begin()

    def next(self):
        if self._idx < len(self._queue) - 1:
            self.play_index(self._idx + 1)

    def prev(self):
        if self._idx > 0:
            self.play_index(self._idx - 1)

    def toggle_pause(self):
        if not self._sound:
            return
        if self._sound.state == 'play':
            self._sound.stop()
            self._notify_state(self.STATE_PAUSED)
        else:
            self._sound.play()
            self._notify_state(self.STATE_PLAYING)

    def seek(self, position: float):
        if self._sound:
            self._sound.seek(position)

    @property
    def is_playing(self) -> bool:
        return bool(self._sound and self._sound.state == 'play')

    @property
    def current_track(self) -> dict | None:
        if 0 <= self._idx < len(self._queue):
            return self._queue[self._idx]
        return None

    @property
    def queue(self) -> list:
        return list(self._queue)

    @property
    def current_index(self) -> int:
        return self._idx

    # ── Internal orchestration ────────────────

    def _begin(self):
        track = self.current_track
        if not track:
            return
        self._stop_ev.clear()
        self._full_ready.clear()
        self._full_path  = None
        self._in_swap    = False
        self._notify_state(self.STATE_LOADING)
        threading.Thread(target=self._pipeline, args=(track,), daemon=True).start()

    def _pipeline(self, track: dict):
        """Main playback pipeline — runs in background thread."""
        url = track['url']

        # ── Step 1: 30-second buffer download ──
        buf_path = self._download_buffer(url)

        if self._stop_ev.is_set():
            return

        if buf_path:
            Clock.schedule_once(lambda dt: self._play_path(buf_path, seek_to=0), 0)
        else:
            # Fallback: stream URL directly
            stream = self._get_stream_url(url)
            if stream and not self._stop_ev.is_set():
                Clock.schedule_once(lambda dt: self._play_path(stream, seek_to=0), 0)
            else:
                self._notify_error("Could not load audio")
                return

        # ── Step 2: full track download (parallel) ──
        threading.Thread(target=self._download_full_bg, args=(url,), daemon=True).start()

    # ── Download helpers ──────────────────────

    def _download_buffer(self, url: str) -> str:
        """Download first BUFFER_SECS seconds via yt-dlp --download-sections."""
        uid  = uuid.uuid4().hex[:10]
        base = os.path.join(CACHE_DIR, f"ytbuf_{uid}")
        opts = {
            'format':                'bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best',
            'outtmpl':               f"{base}.%(ext)s",
            'quiet':                 True,
            'no_warnings':           True,
            'download_ranges':       yt_dlp.utils.download_range_func(None, [(0, BUFFER_SECS)]),
            'force_keyframes_at_cuts': True,
            'postprocessors':        [],    # skip any post-processing
            'retries':               3,
            'logger': SilentLogger(),
            'ffmpeg_location': ffmpeg_path,
            'format': 'bestaudio[ext=m4a]/bestaudio',
        }
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])
            path = self._resolve_output(base)
            if path:
                self._tmps.append(path)
            return path or ''
        except Exception as exc:
            print(f"[Buffer DL] {exc}")
            return ''

    def _download_full_bg(self, url: str):
        """Download full track in background thread, then signal ready."""
        if self._stop_ev.is_set():
            return
        uid  = uuid.uuid4().hex[:10]
        base = os.path.join(CACHE_DIR, f"ytfull_{uid}")
        opts = {
            'format':      'bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best',
            'outtmpl':     f"{base}.%(ext)s",
            'quiet':       True,
            'no_warnings': True,
            'postprocessors': [],
            'retries':     3,
            'logger': SilentLogger(),
            'ffmpeg_location': ffmpeg_path,
            'format': 'bestaudio[ext=m4a]/bestaudio',
        }
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])
            if self._stop_ev.is_set():
                return
            path = self._resolve_output(base)
            if path:
                self._tmps.append(path)
                self._full_path = path
                self._full_ready.set()
                print(f"[Full DL] Ready: {path}")
        except Exception as exc:
            print(f"[Full DL] {exc}")
            # No set() — _handle_buffer_end will fall through to advance

    def _get_stream_url(self, url: str) -> str:
        """Fallback: extract direct CDN audio URL (no download needed)."""
        opts = {
            'format':      'bestaudio[ext=m4a]/bestaudio/best',
            'quiet':       True,
            'skip_download': True,
            'logger': SilentLogger(),
            'ffmpeg_location': ffmpeg_path,
    
        }
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
            # Prefer audio-only formats
            for fmt in reversed(info.get('formats', [])):
                if fmt.get('acodec') != 'none' and fmt.get('vcodec') in (None, 'none'):
                    return fmt.get('url', '')
            return info.get('url', '')
        except Exception as exc:
            print(f"[Stream URL] {exc}")
            return ''

    def _resolve_output(self, base: str) -> str:
        """Find the actual file yt-dlp created (it appends the real extension)."""
        for ext in ('m4a', 'webm', 'opus', 'mp3', 'ogg', 'aac', 'wav'):
            p = f"{base}.{ext}"
            if os.path.exists(p) and os.path.getsize(p) > 1024:
                return p
        # Scan directory for anything starting with our uid token
        d = os.path.dirname(base)
        b = os.path.basename(base)
        try:
            for fname in os.listdir(d):
                if fname.startswith(b):
                    full = os.path.join(d, fname)
                    if os.path.isfile(full) and os.path.getsize(full) > 1024:
                        return full
        except Exception:
            pass
        return ''

    # ── Sound loading & playback ──────────────

    def _play_path(self, path: str, seek_to: float = 0):
        """Load and play an audio file or URL. Must run on main thread (Clock)."""
        if self._stop_ev.is_set():
            return
        if self._sound:
            self._sound.stop()
            self._sound.unload()
            self._sound = None

        snd = SoundLoader.load(path)
        if not snd:
            self._notify_error(f"SoundLoader could not open: {os.path.basename(path)}")
            return

        self._sound = snd
        snd.bind(on_stop=self._on_sound_stop)
        snd.play()
        if seek_to > 0:
            snd.seek(seek_to)

        self._notify_state(self.STATE_PLAYING)
        self._start_progress()
        print(f"[Player] Playing from {seek_to:.1f}s — {os.path.basename(path)}")

    def _on_sound_stop(self, sound_instance):
        """Called by Kivy when sound finishes (or when we call .stop())."""
        if self._stop_ev.is_set():
            return
        # Delay slightly so get_pos() is still valid
        Clock.schedule_once(lambda dt: self._handle_buffer_end(), 0.1)

    def _handle_buffer_end(self):
        """
        The buffer (or current sound) finished.
        Decide: swap to full track, wait for it, or advance queue.
        """
        if self._stop_ev.is_set() or self._in_swap:
            return

        pos = 0
        if self._sound:
            try:
                pos = self._sound.get_pos()
            except Exception:
                pass

        if self._full_ready.is_set() and self._full_path:
            # Full track already downloaded — swap immediately
            self._in_swap = True
            self._play_path(self._full_path, seek_to=pos)
        else:
            # Full download still in progress — wait for it in a thread
            threading.Thread(
                target=self._wait_for_full, args=(pos,), daemon=True
            ).start()

    def _wait_for_full(self, resume_pos: float):
        """Wait up to 60 s for full download, then swap or advance."""
        ready = self._full_ready.wait(timeout=60)
        if self._stop_ev.is_set():
            return
        if ready and self._full_path and not self._in_swap:
            self._in_swap = True
            Clock.schedule_once(
                lambda dt: self._play_path(self._full_path, seek_to=resume_pos), 0
            )
        else:
            # Full download failed / timed out → next track
            Clock.schedule_once(lambda dt: self._advance_queue(), 0)

    def _advance_queue(self):
        if self._stop_ev.is_set():
            return
        if self._idx < len(self._queue) - 1:
            self.play_index(self._idx + 1)
        else:
            self._notify_state(self.STATE_STOPPED)

    # ── Hard stop ────────────────────────────

    def _hard_stop(self, keep_queue: bool = False):
        """Terminate everything cleanly."""
        self._stop_ev.set()
        self._full_ready.set()    # Unblock any waiting thread
        self._cancel_progress()

        snd = self._sound
        if snd:
            try:
                snd.stop()
                snd.unload()
            except Exception:
                pass
            self._sound = None

        if not keep_queue:
            self._queue.clear()
            self._idx = -1

        # Small sleep so background threads see _stop_ev
        time.sleep(0.08)
        self._stop_ev.clear()
        self._full_ready.clear()
        self._full_path  = None
        self._in_swap    = False

        self._cleanup_tmps()

    # ── Progress ticker ───────────────────────

    def _start_progress(self):
        self._cancel_progress()
        self._prog_ev = Clock.schedule_interval(self._tick_progress, 0.5)

    def _cancel_progress(self):
        if self._prog_ev:
            self._prog_ev.cancel()
            self._prog_ev = None

    def _tick_progress(self, dt):
        if self._sound and self._sound.state == 'play' and self.on_progress:
            try:
                pos    = self._sound.get_pos()
                length = self._sound.length or 0
                self.on_progress(pos, length)
            except Exception:
                pass

    # ── Callbacks ─────────────────────────────

    def _notify_state(self, state: str):
        if self.on_state:
            track = self.current_track
            Clock.schedule_once(lambda dt: self.on_state(state, track), 0)

    def _notify_error(self, msg: str):
        print(f"[Player Error] {msg}")
        if self.on_error:
            Clock.schedule_once(lambda dt: self.on_error(msg), 0)

    # ── Cleanup ───────────────────────────────

    def _cleanup_tmps(self):
        for p in list(self._tmps):
            try:
                if p and os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass
        self._tmps.clear()


# ═══════════════════════════════════════════════
#  UI HELPERS
# ═══════════════════════════════════════════════

def attach_bg(widget, color):
    """Attach a solid colored background to any widget."""
    with widget.canvas.before:
        clr = Color(*color)
        rect = Rectangle(size=widget.size, pos=widget.pos)
    widget.bind(
        size=lambda w, s: setattr(rect, 'size', s),
        pos=lambda w, p: setattr(rect, 'pos', p),
    )
    return rect, clr


def lbl(text='', size=14, color=C_WHITE, bold=False, halign='left', valign='middle', **kw):
    """Convenience label factory."""
    l = Label(
        text=text, font_size=sp(size), color=color, bold=bold,
        halign=halign, valign=valign, **kw
    )
    l.bind(size=lambda w, s: setattr(w, 'text_size', s))
    return l


def btn(text, bg=C_ACCENT, fg=C_WHITE, size=14, radius=6, **kw):
    """Convenience button factory."""
    b = Button(
        text=text, font_size=sp(size), color=fg,
        background_color=bg, background_normal='',
        border=(radius, radius, radius, radius),
        **kw
    )
    return b


def fmt_time(secs: float) -> str:
    s = max(0, int(secs))
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


# ═══════════════════════════════════════════════
#  RESULT CARD WIDGET
# ═══════════════════════════════════════════════
class ResultCard(ButtonBehavior, BoxLayout):
    """
    Single search result row:
      [Thumbnail 80px] [Title / Channel · Duration]  [▶ Play]
    """
    HIGHLIGHT = (0.18, 0.18, 0.22, 1)

    def __init__(self, track: dict, on_play, is_active=False, **kw):
        super().__init__(
            orientation='horizontal',
            size_hint_y=None, height=dp(76),
            padding=[dp(8), dp(6)], spacing=dp(8),
            **kw
        )
        self.track    = track
        self._on_play = on_play
        self._bg_rect, self._bg_color = attach_bg(self, C_ELEVATED if is_active else C_SURFACE)

        # Thumbnail
        thumb = AsyncImage(
            source=track.get('thumb', ''),
            size_hint=(None, None), size=(dp(96), dp(64)),
            allow_stretch=True, keep_ratio=True,
        )
        self.add_widget(thumb)

        # Metadata
        meta = BoxLayout(orientation='vertical', spacing=dp(2))
        title_txt = track.get('title', 'Unknown')
        meta.add_widget(lbl(title_txt, size=13, bold=True, color=C_WHITE))
        sub = f"{track.get('channel', '')}  ·  {track.get('duration', '--:--')}"
        meta.add_widget(lbl(sub, size=11, color=C_GREY))
        if track.get('views'):
            meta.add_widget(lbl(track['views'], size=10, color=C_ACCENT_DIM))
        self.add_widget(meta)

        # Play button
        pb = btn('▶', size_hint=(None, 1), width=dp(44), size=16)
        pb.bind(on_press=lambda *a: on_play(track))
        self.add_widget(pb)

        # Divider
        with self.canvas.after:
            Color(*C_DIVIDER)
            Line(points=[0, 0, 0, 0], width=1)

        def _update_divider(*a):
            with self.canvas.after:
                self.canvas.after.clear()
                Color(*C_DIVIDER)
                Line(points=[self.x, self.y, self.right, self.y], width=0.8)
        self.bind(pos=_update_divider, size=_update_divider)

    def on_press(self):
        self._on_play(self.track)

    def set_active(self, active: bool):
        self._bg_color.rgba = C_ELEVATED if active else C_SURFACE


# ═══════════════════════════════════════════════
#  PLAYER BAR  (bottom control strip)
# ═══════════════════════════════════════════════
class PlayerBar(BoxLayout):
    """
    Now-playing bar fixed at the bottom:
      Row 1: [thumb] title / channel
      Row 2: [⏮] [⏸/▶] [⏭]
      Row 3: 0:00 ████░░░░ 3:45   (buffering indicator in label)
    """
    def __init__(self, player: AudioPlayer, **kw):
        super().__init__(
            orientation='vertical',
            size_hint_y=None, height=dp(138),
            padding=[dp(10), dp(6)], spacing=dp(4),
            **kw
        )
        attach_bg(self, C_ELEVATED)
        self._player = player

        # ── Row 1: Track info ──────────────────
        info_row = BoxLayout(orientation='horizontal', spacing=dp(8),
                             size_hint_y=None, height=dp(52))

        self._thumb = AsyncImage(
            source='', size_hint=(None, None), size=(dp(48), dp(48)),
            allow_stretch=True, keep_ratio=True,
        )
        info_row.add_widget(self._thumb)

        meta = BoxLayout(orientation='vertical', spacing=dp(2))
        self._title_lbl   = lbl('Not playing', size=13, bold=True, color=C_WHITE)
        self._channel_lbl = lbl('', size=11, color=C_GREY)
        self._status_lbl  = lbl('', size=10, color=C_ACCENT)
        meta.add_widget(self._title_lbl)
        meta.add_widget(self._channel_lbl)
        meta.add_widget(self._status_lbl)
        info_row.add_widget(meta)

        # ── Row 2: Controls ────────────────────
        ctrl = BoxLayout(orientation='horizontal', spacing=dp(6),
                         size_hint_y=None, height=dp(44))
        self._prev_btn = btn('⏮', size_hint_x=0.22, size=18, bg=C_SURFACE)
        self._play_btn = btn('▶', size_hint_x=0.56, size=20, bg=C_ACCENT)
        self._next_btn = btn('⏭', size_hint_x=0.22, size=18, bg=C_SURFACE)
        self._prev_btn.bind(on_press=lambda *a: player.prev())
        self._play_btn.bind(on_press=lambda *a: player.toggle_pause())
        self._next_btn.bind(on_press=lambda *a: player.next())
        ctrl.add_widget(self._prev_btn)
        ctrl.add_widget(self._play_btn)
        ctrl.add_widget(self._next_btn)

        # ── Row 3: Progress bar ────────────────
        prog_row = BoxLayout(orientation='horizontal', spacing=dp(6),
                             size_hint_y=None, height=dp(28))
        self._pos_lbl = lbl('0:00', size=11, color=C_GREY,
                            size_hint_x=None, width=dp(38), halign='right')
        self._bar     = ProgressBar(max=1, value=0)
        self._dur_lbl = lbl('0:00', size=11, color=C_GREY,
                            size_hint_x=None, width=dp(38), halign='left')
        prog_row.add_widget(self._pos_lbl)
        prog_row.add_widget(self._bar)
        prog_row.add_widget(self._dur_lbl)

        self.add_widget(info_row)
        self.add_widget(ctrl)
        self.add_widget(prog_row)

    # ── Public update methods ─────────────────

    def update_track(self, track: dict | None):
        if track:
            self._title_lbl.text   = track.get('title', 'Unknown')
            self._channel_lbl.text = track.get('channel', '')
            self._thumb.source     = track.get('thumb', '')
        else:
            self._title_lbl.text   = 'Not playing'
            self._channel_lbl.text = ''
            self._thumb.source     = ''

    def update_state(self, state: str):
        state_cfg = {
            AudioPlayer.STATE_IDLE:    ('▶',  C_ACCENT,     ''),
            AudioPlayer.STATE_LOADING: ('⏳', C_SURFACE,    '⬇  Buffering 30s…'),
            AudioPlayer.STATE_PLAYING: ('⏸',  C_ACCENT,     '▶  Playing'),
            AudioPlayer.STATE_PAUSED:  ('▶',  C_ACCENT_DIM, '⏸  Paused'),
            AudioPlayer.STATE_STOPPED: ('▶',  C_ACCENT,     '■  Stopped'),
            AudioPlayer.STATE_ERROR:   ('⚠',  C_WARNING,    '⚠  Error'),
        }
        icon, bg, status = state_cfg.get(state, ('▶', C_ACCENT, ''))
        self._play_btn.text             = icon
        self._play_btn.background_color = bg
        self._status_lbl.text           = status

    def update_progress(self, pos: float, length: float):
        self._pos_lbl.text = fmt_time(pos)
        self._dur_lbl.text = fmt_time(length)
        self._bar.max      = max(length, 1)
        self._bar.value    = pos


# ═══════════════════════════════════════════════
#  QUEUE PANEL (slide-in queue viewer)
# ═══════════════════════════════════════════════
class QueuePanel(BoxLayout):
    """
    Shows the current play queue.  Tap any item to jump to it.
    """
    def __init__(self, player: AudioPlayer, **kw):
        super().__init__(orientation='vertical', **kw)
        attach_bg(self, C_BG)
        self._player = player

        header = BoxLayout(size_hint_y=None, height=dp(44), padding=dp(8))
        attach_bg(header, C_ELEVATED)
        header.add_widget(lbl('Queue', size=15, bold=True))
        self.add_widget(header)

        self._list_box = BoxLayout(orientation='vertical', size_hint_y=None, spacing=dp(2))
        self._list_box.bind(minimum_height=self._list_box.setter('height'))
        sv = ScrollView()
        sv.add_widget(self._list_box)
        self.add_widget(sv)

    def refresh(self, queue: list, current_idx: int):
        self._list_box.clear_widgets()
        if not queue:
            self._list_box.add_widget(lbl('Queue is empty', size=13, color=C_GREY, halign='center'))
            return
        for i, track in enumerate(queue):
            row = BoxLayout(orientation='horizontal', size_hint_y=None, height=dp(48),
                            padding=[dp(8), 0], spacing=dp(6))
            attach_bg(row, C_ELEVATED if i == current_idx else C_SURFACE)
            num = lbl(str(i + 1), size=11, color=C_ACCENT if i == current_idx else C_GREY,
                      size_hint_x=None, width=dp(24), halign='right')
            title = lbl(track.get('title', ''), size=12,
                        bold=(i == current_idx), color=C_WHITE if i == current_idx else C_GREY)
            dur   = lbl(track.get('duration', ''), size=11, color=C_GREY,
                        size_hint_x=None, width=dp(44), halign='right')
            idx = i
            row.bind(on_touch_down=lambda w, t, i=idx: self._jump(w, t, i))
            row.add_widget(num)
            row.add_widget(title)
            row.add_widget(dur)
            self._list_box.add_widget(row)

    def _jump(self, widget, touch, idx):
        if widget.collide_point(*touch.pos):
            self._player.play_index(idx)


# ═══════════════════════════════════════════════
#  MAIN SCREEN  (root widget)
# ═══════════════════════════════════════════════
class MainScreen(FloatLayout):
    """
    Layout:
    ┌──────────────────────────────────────────┐
    │  [🎵 YTMusic]   [≡ Queue]                │  ← header
    ├──────────────────────────────────────────┤
    │  [Search input ……………………………] [🔍]       │  ← search bar
    ├──────────────────────────────────────────┤
    │                                          │
    │         Search results (scroll)          │  ← content
    │                                          │
    ├──────────────────────────────────────────┤
    │  [thumb] Title / channel  ▶ status       │  ← player bar
    │  [⏮]          [⏸]          [⏭]          │
    │  0:30  ████████░░░░░░░░░░  3:45          │
    └──────────────────────────────────────────┘
    """
    def __init__(self, **kw):
        super().__init__(**kw)
        attach_bg(self, C_BG)

        self._searcher    = YouTubeSearcher()
        self._player      = AudioPlayer()
        self._results     = []
        self._result_cards= []
        self._queue_open  = False

        # Wire player callbacks
        self._player.on_state    = self._on_player_state
        self._player.on_progress = self._on_player_progress
        self._player.on_error    = self._on_player_error

        self._build()

    # ── Build UI ─────────────────────────────

    def _build(self):
        # ── Main column (header + search + results + player) ──
        main_col = BoxLayout(
            orientation='vertical',
            size_hint=(1, 1), pos_hint={'x': 0, 'y': 0}
        )

        # Header
        header = BoxLayout(
            orientation='horizontal',
            size_hint_y=None, height=dp(48),
            padding=[dp(12), dp(6)], spacing=dp(8)
        )
        attach_bg(header, C_ELEVATED)
        logo = lbl('🎵 YTMusic', size=16, bold=True, color=C_WHITE, size_hint_x=0.7)
        queue_btn = btn('☰  Queue', bg=C_SURFACE, size=13, size_hint_x=0.3)
        queue_btn.bind(on_press=self._toggle_queue)
        header.add_widget(logo)
        header.add_widget(queue_btn)

        # Search bar
        search_row = BoxLayout(
            orientation='horizontal',
            size_hint_y=None, height=dp(52),
            padding=[dp(8), dp(6)], spacing=dp(8)
        )
        attach_bg(search_row, C_BG)
        self._search_input = TextInput(
            hint_text='Search for songs, artists, albums…',
            multiline=False, size_hint_x=0.82,
            font_size=sp(14),
            background_color=C_SURFACE,
            foreground_color=C_WHITE,
            cursor_color=C_ACCENT,
            hint_text_color=C_GREY,
            padding=[dp(10), dp(10)],
        )
        self._search_input.bind(on_text_validate=lambda *a: self._do_search())
        search_go = btn('🔍', size_hint_x=0.18, size=16)
        search_go.bind(on_press=lambda *a: self._do_search())
        search_row.add_widget(self._search_input)
        search_row.add_widget(search_go)

        # Status label (shows above results)
        self._status_lbl = lbl(
            'Search for music above',
            size=13, color=C_GREY, halign='center',
            size_hint_y=None, height=dp(44)
        )

        # Results scroll view
        self._results_box = BoxLayout(
            orientation='vertical', spacing=dp(3),
            size_hint_y=None, padding=[0, dp(2)]
        )
        self._results_box.bind(minimum_height=self._results_box.setter('height'))
        self._results_box.add_widget(self._status_lbl)

        self._scroll = ScrollView(size_hint=(1, 1))
        self._scroll.add_widget(self._results_box)

        # Player bar
        self._player_bar = PlayerBar(self._player)

        main_col.add_widget(header)
        main_col.add_widget(search_row)
        main_col.add_widget(self._scroll)
        main_col.add_widget(self._player_bar)

        # ── Queue panel (hidden by default, overlays on right) ──
        self._queue_panel = QueuePanel(
            self._player,
            size_hint=(0.75, 1),
            pos_hint={'right': 0, 'y': 0},  # starts off-screen
        )
        self._queue_panel.opacity = 0

        self.add_widget(main_col)
        self.add_widget(self._queue_panel)

    # ── Search ───────────────────────────────

    def _do_search(self):
        q = self._search_input.text.strip()
        if not q:
            return
        self._show_status(f'Searching "{q}"…')
        threading.Thread(target=self._search_thread, args=(q,), daemon=True).start()

    def _search_thread(self, query: str):
        results = self._searcher.search(query)
        Clock.schedule_once(lambda dt: self._show_results(results), 0)

    def _show_status(self, msg: str):
        self._results_box.clear_widgets()
        self._status_lbl.text = msg
        self._results_box.add_widget(self._status_lbl)

    def _show_results(self, results: list):
        self._results      = results
        self._result_cards = []
        self._results_box.clear_widgets()

        if not results:
            self._show_status('No results found. Try a different search.')
            return

        active_idx = self._player.current_index
        active_id  = self._player.current_track.get('id') if self._player.current_track else None

        for i, track in enumerate(results):
            is_active = (track.get('id') == active_id)
            card = ResultCard(
                track,
                on_play=lambda t, idx=i: self._play_track(t, idx),
                is_active=is_active,
            )
            self._result_cards.append(card)
            self._results_box.add_widget(card)

    # ── Playback ──────────────────────────────

    def _play_track(self, track: dict, idx: int):
        self._player.play_queue(self._results, start=idx)
        self._player_bar.update_track(track)
        self._highlight_card(idx)
        if self._queue_open:
            self._queue_panel.refresh(self._results, idx)

    def _highlight_card(self, active_idx: int):
        for i, card in enumerate(self._result_cards):
            card.set_active(i == active_idx)

    # ── Queue panel toggle ─────────────────

    def _toggle_queue(self, *a):
        self._queue_open = not self._queue_open
        if self._queue_open:
            self._queue_panel.refresh(self._results, self._player.current_index)
            self._queue_panel.pos_hint = {'right': 1, 'y': 0}
            self._queue_panel.opacity = 1
        else:
            self._queue_panel.pos_hint = {'right': 0, 'y': 0}
            self._queue_panel.opacity = 0

    # ── Player callbacks (main thread via Clock) ──

    def _on_player_state(self, state: str, track: dict | None):
        self._player_bar.update_state(state)
        if track:
            self._player_bar.update_track(track)
            # Highlight active card
            active_id = track.get('id')
            for i, card in enumerate(self._result_cards):
                card.set_active(card.track.get('id') == active_id)
        if self._queue_open:
            self._queue_panel.refresh(self._results, self._player.current_index)

    def _on_player_progress(self, pos: float, length: float):
        self._player_bar.update_progress(pos, length)

    def _on_player_error(self, msg: str):
        self._player_bar.update_state(AudioPlayer.STATE_ERROR)
        self._show_status(f'⚠  {msg}')


# ═══════════════════════════════════════════════
#  APPLICATION
# ═══════════════════════════════════════════════
class YTMusicApp(App):
    title = 'YTMusic'

    def build(self):
        Window.clearcolor = C_BG
        # Allow landscape & portrait
        if platform != 'android':
            Window.size = (400, 780)   # Desktop preview size
        return MainScreen()

    def on_pause(self):
        """
        Android: returning True keeps the app alive in the background.
        Audio continues playing via Kivy's SoundLoader (Android MediaPlayer).

        NOTE: For a fully persistent background service that survives
        swiping the app away, you'd need a python-for-android Service
        (see buildozer.spec service = ...).  This approach keeps audio
        alive as long as the OS doesn't kill the process.
        """
        return True

    def on_resume(self):
        pass

    def on_stop(self):
        if hasattr(self, '_root') and hasattr(self._root, '_player'):
            self._root._player._hard_stop()


# ─────────────────────────────────────────────
if __name__ == '__main__':
    YTMusicApp().run()
