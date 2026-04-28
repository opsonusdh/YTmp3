#!/usr/bin/env python3
"""
YTMusicPlayer — Background YouTube music player for Android
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  GUI    : Kivy  (UI + audio — single framework, no pygame)
  Search : yt-dlp ytsearch extractor
  Audio  : yt-dlp CDN stream URL → Kivy SoundLoader (Android MediaPlayer)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

FIX LOG v1.1
────────────
BUG 1  get_ffmpeg_path() had no try/except — crashed at module level on
       Android → "WINDOW DIED" before any UI appeared.  FIXED: wrapped.

BUG 2  `return results` was INSIDE the for-loop (wrong indent) → only
       ever returned 1 result.  FIXED: moved outside loop.

BUG 3  search() returned None (implicit) on exception instead of [].
       FIXED: explicit `return []` in except block.

BUG 4  `dict | None` union syntax requires Python 3.10+.  p4a builds
       can ship slightly older runtimes.
       FIXED: `from __future__ import annotations` at top makes ALL
       annotations lazy strings — safe on any Python 3.7+.

BUG 5  allow_stretch / keep_ratio deprecated in Kivy 2.3.
       FIXED: replaced with fit_mode='contain'.

BUG 6  Mobile User-Agent caused 302 redirect to m.youtube.com.
       Not relevant here since search is now yt-dlp based, but the
       leftover mobile UA is removed anyway.

DESIGN CHANGE
       Dropped 30-second buffer pipeline (required ffmpeg on device).
       Now: yt-dlp extracts CDN stream URL → SoundLoader streams it.
       Android MediaPlayer buffers natively (~2 s to first audio).
       No ffmpeg needed at all at playback time.
"""

# from __future__ must be the VERY FIRST statement after docstring/comments
from __future__ import annotations   # FIX 4: dict|None safe on Python <3.10

# ─────────────────────────────────────────────
#  STDLIB
# ─────────────────────────────────────────────
import os
import tempfile
import threading
import time
import traceback

# ─────────────────────────────────────────────
#  THIRD-PARTY
# ─────────────────────────────────────────────
import yt_dlp

# ─────────────────────────────────────────────
#  KIVY — configure BEFORE any other kivy import
# ─────────────────────────────────────────────
from kivy.config import Config
Config.set('kivy', 'keyboard_mode', 'systemanddock')

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
from kivy.uix.image import AsyncImage
from kivy.uix.label import Label
from kivy.uix.progressbar import ProgressBar
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput
from kivy.utils import platform


# ═══════════════════════════════════════════════
#  CONFIG
# ═══════════════════════════════════════════════

# BUG 1 FIX ── every Android import wrapped in try/except ─────────────────────
CACHE_DIR = tempfile.gettempdir()
if platform == 'android':
    try:
        from android.storage import app_storage_path   # type: ignore
        CACHE_DIR = app_storage_path()
    except Exception:
        pass


def _get_ffmpeg_path() -> str:
    """Return path to ffmpeg binary; never raises."""
    if platform == 'android':
        try:
            from android.storage import app_storage_path  # type: ignore
            return os.path.join(app_storage_path(), 'ffmpeg')
        except Exception:
            pass
    return 'ffmpeg'   # system PATH on desktop / safe fallback on Android


FFMPEG_PATH = _get_ffmpeg_path()   # safe: function never raises

# ─────────────────────────────────────────────
#  PALETTE  (AMOLED dark)
# ─────────────────────────────────────────────
C_BG         = (0.05, 0.05, 0.07, 1)
C_SURFACE    = (0.11, 0.11, 0.14, 1)
C_ELEVATED   = (0.16, 0.16, 0.20, 1)
C_ACCENT     = (0.92, 0.18, 0.22, 1)
C_ACCENT_DIM = (0.55, 0.10, 0.13, 1)
C_WHITE      = (1.00, 1.00, 1.00, 1)
C_GREY       = (0.55, 0.55, 0.62, 1)
C_DIVIDER    = (0.20, 0.20, 0.24, 1)
C_WARNING    = (0.95, 0.61, 0.07, 1)


# ─────────────────────────────────────────────
#  SMALL HELPERS
# ─────────────────────────────────────────────
def _fmt_duration(seconds) -> str:
    if not seconds:
        return '--:--'
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f'{h}:{m:02d}:{s:02d}' if h else f'{m}:{s:02d}'


def _fmt_views(views) -> str:
    if not views:
        return ''
    if views >= 1_000_000:
        return f'{views / 1_000_000:.1f}M views'
    if views >= 1_000:
        return f'{views / 1_000:.1f}K views'
    return f'{views} views'


def fmt_time(secs: float) -> str:
    s = max(0, int(secs))
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    return f'{h}:{m:02d}:{s:02d}' if h else f'{m}:{s:02d}'


# ═══════════════════════════════════════════════
#  YT-DLP SILENT LOGGER
# ═══════════════════════════════════════════════
class SilentLogger:
    def debug(self, msg):   pass
    def warning(self, msg): print('[yt-dlp]', msg)
    def error(self, msg):   print('[yt-dlp ERROR]', msg)


# ═══════════════════════════════════════════════
#  YOUTUBE SEARCHER
# ═══════════════════════════════════════════════
class YouTubeSearcher:
    """
    yt-dlp ytsearchN: fetches search results directly — no HTTP scraping,
    no BeautifulSoup, no User-Agent redirect issues.
    """
    THUMB_FALLBACK = 'https://img.youtube.com/vi/{}/mqdefault.jpg'

    def search(self, query: str) -> list:
        opts = {
            'quiet':         True,
            'skip_download': True,
            'noplaylist':    True,
            'ignoreerrors':  True,
            'logger':        SilentLogger(),
            'ffmpeg_location': FFMPEG_PATH,
            'format':        'bestaudio[ext=m4a]/bestaudio',
        }
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(f'ytsearch10:{query}', download=False)

            if not info:
                return []                        # FIX 3

            results = []
            for entry in (info.get('entries') or []):   # FIX 2: loop outside return
                if not entry:
                    continue
                vid = entry.get('id')
                if not vid:
                    continue
                results.append({
                    'id':       vid,
                    'url':      (entry.get('webpage_url') or
                                 f'https://www.youtube.com/watch?v={vid}'),
                    'title':    entry.get('title') or 'Unknown',
                    'channel':  entry.get('uploader') or '',
                    'duration': _fmt_duration(entry.get('duration')),
                    'views':    _fmt_views(entry.get('view_count')),
                    'thumb':    (entry.get('thumbnail') or
                                 self.THUMB_FALLBACK.format(vid)),
                })

            return results                       # FIX 2: this is OUTSIDE the loop

        except Exception as exc:
            print(f'[Searcher] {exc}')
            traceback.print_exc()
            return []                            # FIX 3: was implicit None


# ═══════════════════════════════════════════════
#  AUDIO PLAYER
# ═══════════════════════════════════════════════
class AudioPlayer:
    """
    Queue-based player.  No ffmpeg required on the device.

    Pipeline per track:
      Thread → yt-dlp extracts CDN stream URL (no download)
      Main   → SoundLoader.load(cdn_url).play()
      Android MediaPlayer streams + buffers natively (~2 s start time)
    """

    STATE_IDLE    = 'idle'
    STATE_LOADING = 'loading'
    STATE_PLAYING = 'playing'
    STATE_PAUSED  = 'paused'
    STATE_STOPPED = 'stopped'
    STATE_ERROR   = 'error'

    def __init__(self):
        self.on_state:    object = None
        self.on_progress: object = None
        self.on_error:    object = None

        self._queue:   list              = []
        self._idx:     int               = -1
        self._sound:   object            = None
        self._stop_ev: threading.Event   = threading.Event()
        self._prog_ev: object            = None

    # ── Public API ────────────────────────────────────────────────────────────

    def play_queue(self, tracks: list, start: int = 0):
        self._hard_stop()
        self._queue = list(tracks)
        self._idx   = max(0, min(start, len(tracks) - 1))
        self._begin()

    def play_index(self, idx: int):
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
            try:
                self._sound.seek(position)
            except Exception:
                pass

    @property
    def is_playing(self) -> bool:
        return bool(self._sound and self._sound.state == 'play')

    @property
    def current_track(self) -> dict | None:   # FIX 4: safe via __future__
        if 0 <= self._idx < len(self._queue):
            return self._queue[self._idx]
        return None

    @property
    def queue(self) -> list:
        return list(self._queue)

    @property
    def current_index(self) -> int:
        return self._idx

    # ── Internal ──────────────────────────────────────────────────────────────

    def _begin(self):
        track = self.current_track
        if not track:
            return
        self._stop_ev.clear()
        self._notify_state(self.STATE_LOADING)
        threading.Thread(target=self._pipeline, args=(track,), daemon=True).start()

    def _pipeline(self, track: dict):
        url        = track.get('url', '')
        stream_url = self._get_stream_url(url)

        if self._stop_ev.is_set():
            return
        if not stream_url:
            self._notify_error(f'Could not extract audio: {track.get("title")}')
            return

        Clock.schedule_once(lambda dt: self._play_url(stream_url), 0)

    def _get_stream_url(self, url: str) -> str:
        opts = {
            'format':        'bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best',
            'quiet':         True,
            'no_warnings':   True,
            'skip_download': True,
            'logger':        SilentLogger(),
            'ffmpeg_location': FFMPEG_PATH,
            'extractor_args': {
                'youtube': {'player_client': ['android', 'web']}
            },
        }
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)

            for fmt in reversed(info.get('formats') or []):
                if (fmt.get('acodec') not in (None, 'none') and
                        fmt.get('vcodec') in (None, 'none', '') and
                        fmt.get('url')):
                    return fmt['url']
            return info.get('url', '')
        except Exception as exc:
            print(f'[Player] stream extraction error: {exc}')
            traceback.print_exc()
            return ''

    def _play_url(self, url: str):
        """Must run on Kivy main thread."""
        if self._stop_ev.is_set():
            return
        self._release_sound()

        snd = SoundLoader.load(url)
        if not snd:
            self._notify_error('SoundLoader could not open stream')
            return

        self._sound = snd
        snd.bind(on_stop=self._on_sound_stop)
        snd.play()
        self._notify_state(self.STATE_PLAYING)
        self._start_progress()

    def _on_sound_stop(self, *args):
        if not self._stop_ev.is_set():
            Clock.schedule_once(lambda dt: self._advance_queue(), 0.2)

    def _advance_queue(self):
        if self._stop_ev.is_set():
            return
        if self._idx < len(self._queue) - 1:
            self.play_index(self._idx + 1)
        else:
            self._notify_state(self.STATE_STOPPED)

    def _hard_stop(self, keep_queue: bool = False):
        self._stop_ev.set()
        self._cancel_progress()
        self._release_sound()
        if not keep_queue:
            self._queue.clear()
            self._idx = -1
        time.sleep(0.05)
        self._stop_ev.clear()

    def _release_sound(self):
        snd, self._sound = self._sound, None
        if snd:
            try:
                snd.stop()
                snd.unload()
            except Exception:
                pass

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
                self.on_progress(self._sound.get_pos(), self._sound.length or 0)
            except Exception:
                pass

    def _notify_state(self, state: str):
        if self.on_state:
            track = self.current_track
            Clock.schedule_once(lambda dt: self.on_state(state, track), 0)

    def _notify_error(self, msg: str):
        print(f'[Player Error] {msg}')
        if self.on_error:
            Clock.schedule_once(lambda dt: self.on_error(msg), 0)


# ═══════════════════════════════════════════════
#  UI HELPERS
# ═══════════════════════════════════════════════

def attach_bg(widget, color):
    with widget.canvas.before:
        clr  = Color(*color)
        rect = Rectangle(size=widget.size, pos=widget.pos)
    widget.bind(
        size=lambda w, s: setattr(rect, 'size', s),
        pos =lambda w, p: setattr(rect, 'pos',  p),
    )
    return rect, clr


def lbl(text='', size=14, color=C_WHITE, bold=False,
        halign='left', valign='middle', **kw):
    l = Label(text=text, font_size=sp(size), color=color, bold=bold,
              halign=halign, valign=valign, **kw)
    l.bind(size=lambda w, s: setattr(w, 'text_size', s))
    return l


def btn(text, bg=C_ACCENT, fg=C_WHITE, size=14, radius=6, **kw):
    return Button(
        text=text, font_size=sp(size), color=fg,
        background_color=bg, background_normal='',
        border=(radius, radius, radius, radius), **kw,
    )


# ═══════════════════════════════════════════════
#  RESULT CARD
# ═══════════════════════════════════════════════
class ResultCard(ButtonBehavior, BoxLayout):
    def __init__(self, track: dict, on_play, is_active=False, **kw):
        super().__init__(
            orientation='horizontal',
            size_hint_y=None, height=dp(76),
            padding=[dp(8), dp(6)], spacing=dp(8), **kw,
        )
        self.track    = track
        self._on_play = on_play
        self._bg_rect, self._bg_color = attach_bg(
            self, C_ELEVATED if is_active else C_SURFACE
        )

        # Thumbnail — FIX 5: fit_mode replaces deprecated allow_stretch/keep_ratio
        thumb = AsyncImage(
            source=track.get('thumb', ''),
            size_hint=(None, None), size=(dp(96), dp(64)),
            fit_mode='contain',
        )
        self.add_widget(thumb)

        meta = BoxLayout(orientation='vertical', spacing=dp(2))
        meta.add_widget(lbl(track.get('title', 'Unknown'), size=13, bold=True))
        sub = f"{track.get('channel', '')}  ·  {track.get('duration', '--:--')}"
        meta.add_widget(lbl(sub, size=11, color=C_GREY))
        if track.get('views'):
            meta.add_widget(lbl(track['views'], size=10, color=C_ACCENT_DIM))
        self.add_widget(meta)

        pb = btn('▶', size_hint=(None, 1), width=dp(44), size=16)
        pb.bind(on_press=lambda *a: on_play(track))
        self.add_widget(pb)

        def _divider(*a):
            self.canvas.after.clear()
            with self.canvas.after:
                Color(*C_DIVIDER)
                Line(points=[self.x, self.y, self.right, self.y], width=0.8)
        self.bind(pos=_divider, size=_divider)

    def on_press(self):
        self._on_play(self.track)

    def set_active(self, active: bool):
        self._bg_color.rgba = C_ELEVATED if active else C_SURFACE


# ═══════════════════════════════════════════════
#  PLAYER BAR
# ═══════════════════════════════════════════════
class PlayerBar(BoxLayout):
    def __init__(self, player: AudioPlayer, **kw):
        super().__init__(
            orientation='vertical',
            size_hint_y=None, height=dp(138),
            padding=[dp(10), dp(6)], spacing=dp(4), **kw,
        )
        attach_bg(self, C_ELEVATED)
        self._player = player

        info_row = BoxLayout(orientation='horizontal', spacing=dp(8),
                             size_hint_y=None, height=dp(52))
        self._thumb = AsyncImage(
            source='', size_hint=(None, None), size=(dp(48), dp(48)),
            fit_mode='contain',   # FIX 5
        )
        info_row.add_widget(self._thumb)

        meta = BoxLayout(orientation='vertical', spacing=dp(2))
        self._title_lbl   = lbl('Not playing', size=13, bold=True)
        self._channel_lbl = lbl('', size=11, color=C_GREY)
        self._status_lbl  = lbl('', size=10, color=C_ACCENT)
        meta.add_widget(self._title_lbl)
        meta.add_widget(self._channel_lbl)
        meta.add_widget(self._status_lbl)
        info_row.add_widget(meta)

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

    def update_track(self, track: dict | None):   # FIX 4 via __future__
        if track:
            self._title_lbl.text   = track.get('title', 'Unknown')
            self._channel_lbl.text = track.get('channel', '')
            self._thumb.source     = track.get('thumb', '')
        else:
            self._title_lbl.text   = 'Not playing'
            self._channel_lbl.text = ''
            self._thumb.source     = ''

    def update_state(self, state: str):
        cfg = {
            AudioPlayer.STATE_IDLE:    ('▶',  C_ACCENT,     ''),
            AudioPlayer.STATE_LOADING: ('⏳', C_SURFACE,    '⬇  Fetching stream…'),
            AudioPlayer.STATE_PLAYING: ('⏸',  C_ACCENT,     '▶  Playing'),
            AudioPlayer.STATE_PAUSED:  ('▶',  C_ACCENT_DIM, '⏸  Paused'),
            AudioPlayer.STATE_STOPPED: ('▶',  C_ACCENT,     '■  Stopped'),
            AudioPlayer.STATE_ERROR:   ('⚠',  C_WARNING,    '⚠  Error'),
        }
        icon, bg, status = cfg.get(state, ('▶', C_ACCENT, ''))
        self._play_btn.text             = icon
        self._play_btn.background_color = bg
        self._status_lbl.text           = status

    def update_progress(self, pos: float, length: float):
        self._pos_lbl.text = fmt_time(pos)
        self._dur_lbl.text = fmt_time(length)
        self._bar.max      = max(length, 1)
        self._bar.value    = pos


# ═══════════════════════════════════════════════
#  QUEUE PANEL
# ═══════════════════════════════════════════════
class QueuePanel(BoxLayout):
    def __init__(self, player: AudioPlayer, **kw):
        super().__init__(orientation='vertical', **kw)
        attach_bg(self, C_BG)
        self._player = player

        header = BoxLayout(size_hint_y=None, height=dp(44), padding=dp(8))
        attach_bg(header, C_ELEVATED)
        header.add_widget(lbl('Queue', size=15, bold=True))
        self.add_widget(header)

        self._list_box = BoxLayout(orientation='vertical',
                                   size_hint_y=None, spacing=dp(2))
        self._list_box.bind(minimum_height=self._list_box.setter('height'))
        sv = ScrollView()
        sv.add_widget(self._list_box)
        self.add_widget(sv)

    def refresh(self, queue: list, current_idx: int):
        self._list_box.clear_widgets()
        if not queue:
            self._list_box.add_widget(
                lbl('Queue is empty', size=13, color=C_GREY, halign='center'))
            return
        for i, track in enumerate(queue):
            active = (i == current_idx)
            row = BoxLayout(orientation='horizontal',
                            size_hint_y=None, height=dp(48),
                            padding=[dp(8), 0], spacing=dp(6))
            attach_bg(row, C_ELEVATED if active else C_SURFACE)
            row.add_widget(lbl(str(i + 1), size=11,
                               color=C_ACCENT if active else C_GREY,
                               size_hint_x=None, width=dp(24), halign='right'))
            row.add_widget(lbl(track.get('title', ''), size=12, bold=active,
                               color=C_WHITE if active else C_GREY))
            row.add_widget(lbl(track.get('duration', ''), size=11, color=C_GREY,
                               size_hint_x=None, width=dp(44), halign='right'))
            idx = i
            row.bind(on_touch_down=lambda w, t, i=idx: self._jump(w, t, i))
            self._list_box.add_widget(row)

    def _jump(self, widget, touch, idx):
        if widget.collide_point(*touch.pos):
            self._player.play_index(idx)


# ═══════════════════════════════════════════════
#  MAIN SCREEN
# ═══════════════════════════════════════════════
class MainScreen(FloatLayout):
    def __init__(self, **kw):
        super().__init__(**kw)
        attach_bg(self, C_BG)

        self._searcher     = YouTubeSearcher()
        self._player       = AudioPlayer()
        self._results      = []
        self._result_cards = []
        self._queue_open   = False

        self._player.on_state    = self._on_player_state
        self._player.on_progress = self._on_player_progress
        self._player.on_error    = self._on_player_error

        self._build()

    def _build(self):
        main_col = BoxLayout(orientation='vertical',
                             size_hint=(1, 1), pos_hint={'x': 0, 'y': 0})

        header = BoxLayout(orientation='horizontal',
                           size_hint_y=None, height=dp(48),
                           padding=[dp(12), dp(6)], spacing=dp(8))
        attach_bg(header, C_ELEVATED)
        header.add_widget(lbl('🎵 YTMusic', size=16, bold=True, size_hint_x=0.7))
        queue_btn = btn('☰  Queue', bg=C_SURFACE, size=13, size_hint_x=0.3)
        queue_btn.bind(on_press=self._toggle_queue)
        header.add_widget(queue_btn)

        search_row = BoxLayout(orientation='horizontal',
                               size_hint_y=None, height=dp(52),
                               padding=[dp(8), dp(6)], spacing=dp(8))
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

        self._status_lbl = lbl('Search for music above', size=13,
                                color=C_GREY, halign='center',
                                size_hint_y=None, height=dp(44))
        self._results_box = BoxLayout(orientation='vertical', spacing=dp(3),
                                       size_hint_y=None, padding=[0, dp(2)])
        self._results_box.bind(minimum_height=self._results_box.setter('height'))
        self._results_box.add_widget(self._status_lbl)

        scroll = ScrollView(size_hint=(1, 1))
        scroll.add_widget(self._results_box)

        self._player_bar = PlayerBar(self._player)

        main_col.add_widget(header)
        main_col.add_widget(search_row)
        main_col.add_widget(scroll)
        main_col.add_widget(self._player_bar)

        self._queue_panel = QueuePanel(self._player,
                                        size_hint=(0.75, 1),
                                        pos_hint={'right': 0, 'y': 0})
        self._queue_panel.opacity = 0

        self.add_widget(main_col)
        self.add_widget(self._queue_panel)

    def _do_search(self):
        q = self._search_input.text.strip()
        if not q:
            return
        self._show_status(f'Searching "{q}"…')
        threading.Thread(target=self._search_thread, args=(q,), daemon=True).start()

    def _search_thread(self, query: str):
        results = self._searcher.search(query)
        Clock.schedule_once(lambda dt: self._show_results(results or []), 0)

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

        active_id = (self._player.current_track or {}).get('id')
        for i, track in enumerate(results):
            card = ResultCard(
                track,
                on_play=lambda t, idx=i: self._play_track(t, idx),
                is_active=(track.get('id') == active_id),
            )
            self._result_cards.append(card)
            self._results_box.add_widget(card)

    def _play_track(self, track: dict, idx: int):
        self._player.play_queue(self._results, start=idx)
        self._player_bar.update_track(track)
        self._highlight_card(idx)
        if self._queue_open:
            self._queue_panel.refresh(self._results, idx)

    def _highlight_card(self, active_idx: int):
        for i, card in enumerate(self._result_cards):
            card.set_active(i == active_idx)

    def _toggle_queue(self, *a):
        self._queue_open = not self._queue_open
        if self._queue_open:
            self._queue_panel.refresh(self._results, self._player.current_index)
            self._queue_panel.pos_hint = {'right': 1, 'y': 0}
            self._queue_panel.opacity  = 1
        else:
            self._queue_panel.pos_hint = {'right': 0, 'y': 0}
            self._queue_panel.opacity  = 0

    def _on_player_state(self, state: str, track: dict | None):  # FIX 4 via __future__
        self._player_bar.update_state(state)
        if track:
            self._player_bar.update_track(track)
            aid = track.get('id')
            for card in self._result_cards:
                card.set_active(card.track.get('id') == aid)
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
        if platform != 'android':
            Window.size = (400, 780)
        return MainScreen()

    def on_pause(self):
        return True   # keep process alive → audio continues in background

    def on_resume(self):
        pass

    def on_stop(self):
        root = getattr(self, 'root', None)
        if root and hasattr(root, '_player'):
            root._player._hard_stop()


if __name__ == '__main__':
    try:
        YTMusicApp().run()
    except Exception as exc:
        print(f'[FATAL] {exc}')
        traceback.print_exc()
