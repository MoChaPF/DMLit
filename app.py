#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
DMLit — 只适配 WEIKAV VEGA108 的键盘灯效工具
优化：只发变化块、不等 ACK、20fps，避免占用 HID 带宽导致按键卡顿
"""
import os
import time
import math
import colorsys
import threading
import ctypes
from ctypes import wintypes

import hid
import webview
import numpy as np
import soundcard as sc

VID = 0x0C45
PAGES = (0xFF68, 0xFF60)
KEYS = 128
CHUNK = 56
FPS = 20
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'state.json')
MAX_CHUNKS_PER_FRAME = 10

HERE = os.path.dirname(os.path.abspath(__file__))
LAYOUT_PATH = os.path.join(HERE, "vega108_layout.json")
import json as _json
import json
import sys
import winreg
try:
    LAYOUT = _json.load(open(LAYOUT_PATH, encoding="utf-8"))
except Exception:
    LAYOUT = {"rows": []}

PAINT_PATH = os.path.join(HERE, "vega108_paint.json")

# LED 编号 -> (x, y) 物理坐标
XY = [(0.0, 0.0)] * KEYS
for _row in LAYOUT.get("rows", []):
    for _k in _row:
        _v = _k.get("value", -1)
        if 0 <= _v < KEYS:
            XY[_v] = (float(_k.get("x", 0)), float(_k.get("y", 0)))


def load_paint():
    try:
        return _json.load(open(PAINT_PATH, encoding="utf-8"))
    except Exception:
        return {}


def save_paint(colors):
    try:
        _json.dump(colors, open(PAINT_PATH, "w", encoding="utf-8"), ensure_ascii=False)
    except Exception:
        pass

hid_dll = ctypes.WinDLL('hid.dll', use_last_error=True)
kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                 ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
kernel32.CreateFileW.restype = ctypes.c_void_p
kernel32.ReadFile.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
                              ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
kernel32.WriteFile.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
                               ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
hid_dll.HidD_SetOutputReport.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.ULONG]
hid_dll.HidD_SetOutputReport.restype = wintypes.BOOLEAN
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
OPEN_EXISTING = 3
FILE_FLAG_OVERLAPPED = 0x40000000
ERROR_IO_PENDING = 997


class OVERLAPPED(ctypes.Structure):
    _fields_ = [('Internal', ctypes.c_void_p),
                ('InternalHigh', ctypes.c_void_p),
                ('Offset', wintypes.DWORD),
                ('OffsetHigh', wintypes.DWORD),
                ('hEvent', wintypes.HANDLE)]


kernel32.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.CreateEventW.restype = wintypes.HANDLE
kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel32.WaitForSingleObject.restype = wintypes.DWORD
kernel32.GetOverlappedResult.argtypes = [ctypes.c_void_p, ctypes.POINTER(OVERLAPPED),
                                         ctypes.POINTER(wintypes.DWORD), wintypes.BOOL]
kernel32.GetOverlappedResult.restype = wintypes.BOOL
kernel32.CancelIo.argtypes = [ctypes.c_void_p]
kernel32.CancelIo.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL


class Keyboard:
    def __init__(self):
        self.h = None
        self.hr = None          # 异步读句柄（ReadFile 没有超时，必须用 OVERLAPPED）
        self.page = None
        self.lock = threading.Lock()
        self.io_lock = threading.Lock()   # 读响应必须独占，否则两个线程 ReadFile 会互相抢包
        self.last_chunks = {}
        self.rr = 0
        self.last_pixels = None

    def open(self):
        for page in PAGES:
            for d in hid.enumerate(VID, 0):
                if d.get('usage_page') == page:
                    path = d['path'].decode() if isinstance(d['path'], bytes) else d['path']
                    h = kernel32.CreateFileW(path, GENERIC_READ | GENERIC_WRITE, 3, None, OPEN_EXISTING, 0, None)
                    if h and h != ctypes.c_void_p(-1).value:
                        self.h = h
                        # 另开一个异步读句柄：ReadFile 在没数据时会永久阻塞，
                        # 读必须能超时，否则一次丢包就把整个 IO 锁焊死。
                        hr = kernel32.CreateFileW(path, GENERIC_READ | GENERIC_WRITE, 3, None,
                                                  OPEN_EXISTING, FILE_FLAG_OVERLAPPED, None)
                        if hr and hr != ctypes.c_void_p(-1).value:
                            self.hr = hr
                        self.page = page
                        self.last_chunks = {}
                        return True
        return False

    def close(self):
        if self.hr:
            kernel32.CloseHandle(self.hr)
            self.hr = None
        if self.h:
            kernel32.CloseHandle(self.h)
            self.h = None

    def _read_packet(self, timeout=800):
        """读一个 HID 包，带超时。"""
        if not self.h:
            return None
        if self.hr:
            ov = OVERLAPPED()
            ev = kernel32.CreateEventW(None, True, False, None)
            if not ev:
                return None
            ov.hEvent = ev
            inbuf = (ctypes.c_ubyte * 65)()
            n = wintypes.DWORD(0)
            ok = kernel32.ReadFile(self.hr, ctypes.byref(inbuf), 65, ctypes.byref(n), ctypes.byref(ov))
            if not ok:
                if ctypes.get_last_error() != ERROR_IO_PENDING:
                    kernel32.CloseHandle(ev)
                    return None
                if kernel32.WaitForSingleObject(ev, timeout) != 0:
                    kernel32.CancelIo(self.hr)
                    kernel32.CloseHandle(ev)
                    return None
                if not kernel32.GetOverlappedResult(self.hr, ctypes.byref(ov), ctypes.byref(n), False):
                    kernel32.CloseHandle(ev)
                    return None
            kernel32.CloseHandle(ev)
            return bytes(inbuf[:n.value]) if n.value else None
        # 回退：没有异步句柄时用同步读（可能阻塞）
        inbuf = (ctypes.c_ubyte * 65)()
        n = wintypes.DWORD(0)
        if kernel32.ReadFile(self.h, ctypes.byref(inbuf), 65, ctypes.byref(n), None):
            return bytes(inbuf[:n.value])
        return None

    def _write(self, cmd, length, addr, data, read=False, timeout=120):
        if not self.h:
            return None
        p = bytearray(65)
        p[0] = 0x00   # report id
        p[1] = 0xAA
        p[2] = cmd
        p[3] = length
        p[4] = addr & 0xFF
        p[5] = (addr >> 8) & 0xFF
        p[7] = 1
        if data:
            p[9:9 + len(data)] = data
        buf = (ctypes.c_ubyte * 65).from_buffer_copy(bytes(p))
        n = wintypes.DWORD(0)
        kernel32.WriteFile(ctypes.c_void_p(self.h), ctypes.byref(buf), 65, ctypes.byref(n), None)
        if read:
            inbuf = (ctypes.c_ubyte * 65)()
            n = wintypes.DWORD(0)
            if kernel32.ReadFile(ctypes.c_void_p(self.h), ctypes.byref(inbuf), 65, ctypes.byref(n), None):
                return bytes(inbuf[:n.value])
        return None


    def _request(self, cmd, length, addr, data=b''):
        """只发送不读取。设备的响应比请求滞后一包，所以读取时要用
        「发一次 → 连读几包 → 挑地址匹配的那包」，不能发完只读一次。"""
        if not self.h:
            return
        p = bytearray(65)
        p[0] = 0x00   # report id
        p[1] = 0xAA
        p[2] = cmd
        p[3] = length
        p[4] = addr & 0xFF
        p[5] = (addr >> 8) & 0xFF
        p[7] = 1
        if data:
            p[9:9 + len(data)] = data
        buf = (ctypes.c_ubyte * 65).from_buffer_copy(bytes(p))
        n = wintypes.DWORD(0)
        kernel32.WriteFile(ctypes.c_void_p(self.h), ctypes.byref(buf), 65, ctypes.byref(n), None)

    def _read_match(self, cmd, addr=None, tries=4):
        """读包直到拿到指定命令（可选校验地址）的响应。
        帧头偏移：带 report id 时 base=1（r[1]=0x55），不带时 base=0。"""
        for _ in range(tries):
            r = self._read_packet()
            if not r:
                return None
            if len(r) < 9:
                continue
            base = 0 if r[0] == 0x55 else 1
            if len(r) <= base + 4:
                continue
            if r[base] != 0x55 or r[base + 1] != cmd:
                continue
            if addr is not None and (r[base + 3] | (r[base + 4] << 8)) != addr:
                continue
            return r
        return None

    def _read_leds_24g(self):
        """无线通道读灯色：cmd 59 + ledId 列表，返回 [(r,g,b)] * KEYS。"""
        with self.io_lock:
            try:
                payload = bytearray(KEYS * 2)
                for i in range(KEYS):
                    payload[i * 2] = i & 0xFF
                    payload[i * 2 + 1] = (i >> 8) & 0xFF
                raw = bytearray()
                for addr in range(0, len(payload), CHUNK):
                    n = min(CHUNK, len(payload) - addr)
                    data = bytes(payload[addr:addr + n])
                    got = None
                    for _ in range(3):
                        self._request(59, n, addr, data)
                        got = self._read_match(59, addr)
                        if got:
                            break
                    if not got:
                        return None
                    base = 0 if got[0] == 0x55 else 1
                    raw += got[base + 8:base + 8 + n]
                if len(raw) < KEYS * 2:
                    return None
                colors = []
                for i in range(KEYS):
                    v = (raw[i * 2] << 8) | raw[i * 2 + 1]
                    colors.append([(v >> 11 & 31) << 3, (v >> 5 & 63) << 2, (v & 31) << 3])
                return colors
            except Exception:
                return None

    def battery_24g(self):
        """无线下 cmd 59 响应里顺带带电量（r[6]）。"""
        with self.io_lock:
            try:
                payload = bytearray(2)
                payload[0] = 0
                payload[1] = 0
                self._request(59, 2, 0, bytes(payload))
                got = self._read_match(59, 0)
                if got:
                    return int(got[6])
            except Exception:
                pass
        return None

    def read_leds(self):
        """读当前灯色。有线走 cmd 51，无线（24G）走 cmd 59：
        请求要带 ledId 列表（每个 uint16 小端），响应每 2 字节一个大端 RGB565。"""
        if self.page == 0xFF60:
            return self._read_leds_24g()
        if self.page != 0xFF68:
            return None
        with self.io_lock:
            try:
                payload = bytearray(KEYS * 4)
                for i in range(KEYS):
                    payload[i * 4] = i
                raw = bytearray()
                for addr in range(0, KEYS * 4, CHUNK):
                    n = min(CHUNK, KEYS * 4 - addr)
                    data = bytes(payload[addr:addr + n])
                    got = None
                    for _ in range(3):
                        self._request(51, n, addr, data)
                        got = self._read_match(51, addr)
                        if got:
                            break
                    if not got:
                        return None
                    base = 0 if got[0] == 0x55 else 1
                    raw += got[base + 8:base + 8 + n]
                if len(raw) < 4:
                    return None
                colors = [[0, 0, 0] for _ in range(KEYS)]
                ok = False
                for i in range(0, len(raw) - 3, 4):
                    led = raw[i]
                    if 0 <= led < KEYS:
                        colors[led] = [raw[i + 1], raw[i + 2], raw[i + 3]]
                        ok = True
                return colors if ok else None
            except Exception:
                return None

    def set_mode(self, mode, brightness=5, speed=3, color=None, color_mode=0, direction=3):
        data = bytearray(16)
        data[0] = mode
        if color:
            data[1], data[2], data[3] = color[0], color[1], color[2]
        data[4] = 255          # driverSetting：网页驱动固定 255，缺了无线通道不收
        data[8] = color_mode
        data[9] = brightness
        data[10] = speed
        data[11] = direction
        # data[12..15] 保持 0：网页驱动的 SET_LED_EFFECT 包这两个字节就是 0，
        # 之前误填 AA/55 反而让无线通道整包丢弃（2026-11-08 抓包确认）
        self.last_chunks = {}
        # 只写不读：无线（FF60）通道读响应会无限阻塞，等 ACK 会卡死整个调用
        self._request(35, 16, 0, bytes(data))
        return None

    def _set_pixels_24g(self, pixels):
        """无线通道推灯：cmd 60 + 每灯 RGB565（大端），56 字节一包。"""
        self.last_pixels = list(pixels[:KEYS])
        buf = bytearray(KEYS * 2)
        for i in range(KEYS):
            r, g, b = pixels[i] if i < len(pixels) else (0, 0, 0)
            v = ((int(r) >> 3) << 11) | ((int(g) >> 2) << 5) | (int(b) >> 3)
            buf[i * 2] = (v >> 8) & 0xFF
            buf[i * 2 + 1] = v & 0xFF
        with self.lock:
            chunk = 56
            n = (len(buf) + chunk - 1) // chunk
            for k in range(n):
                addr = k * chunk
                part = bytes(buf[addr:addr + chunk])
                self._request(60, len(part), addr, part)

    def set_pixels(self, pixels, force=False, max_chunks=None):
        """分帧更新：每帧最多发 max_chunks 块，轮转起点避免前面的块霸占"""
        if self.page == 0xFF60:
            return self._set_pixels_24g(pixels)
        limit = max_chunks if max_chunks else MAX_CHUNKS_PER_FRAME
        payload = bytearray(KEYS * 4)
        for i in range(KEYS):
            r, g, b = pixels[i] if i < len(pixels) else (0, 0, 0)
            payload[i * 4] = i
            payload[i * 4 + 1] = r
            payload[i * 4 + 2] = g
            payload[i * 4 + 3] = b

        self.last_pixels = list(pixels[:KEYS])
        with self.lock:
            addrs = list(range(0, KEYS * 4, CHUNK))
            start = self.rr % len(addrs)
            self.rr += 1
            order = addrs[start:] + addrs[:start]
            sent = 0
            for addr in order:
                n = min(CHUNK, KEYS * 4 - addr)
                chunk = bytes(payload[addr:addr + n])
                if force or self.last_chunks.get(addr) != chunk:
                    if (not force) and sent >= limit:
                        break
                    self._write(50, n, addr, chunk, read=False)
                    self.last_chunks[addr] = chunk
                    sent += 1


class EffectEngine:
    def __init__(self, kb):
        self.kb = kb
        self.running = False
        self.name = 'solid'
        self.color = (255, 40, 40)
        self.brightness = 5
        self.speed = 3
        self.xy = XY
        self.thread = None

    def start(self, name):
        self.name = name
        # 先切到自定义模式，否则逐键数据不生效
        self.kb.set_mode(128, self.brightness, self.speed)
        if not self.running:
            self.running = True
            self.thread = threading.Thread(target=self._loop, daemon=True)
            self.thread.start()

    def stop(self):
        self.running = False

    def _pixels(self, t):
        n = self.name
        br = self.brightness / 6.0
        sp = self.speed / 6.0
        r, g, b = self.color
        if n == 'solid':
            return [(int(r * br), int(g * br), int(b * br))] * KEYS
        if n == 'rainbow':
            return [tuple(int(c * 255) for c in colorsys.hsv_to_rgb(((i / KEYS) + t * (0.02 + sp * 0.1)) % 1.0, 1, br)) for i in range(KEYS)]
        if n == 'breathing':
            k = 0.5 + 0.5 * math.sin(t * (1.2 + sp * 4))
            v = br * k
            return [(int(r * v), int(g * v), int(b * v))] * KEYS
        if n == 'wave':
            out = []
            for i in range(KEYS):
                k = 0.5 + 0.5 * math.sin((i / 18.0) - t * (2.0 + sp * 6))
                v = br * (0.15 + 0.85 * k)
                out.append((int(r * v), int(g * v), int(b * v)))
            return out
        if n == 'sparkle':
            return [tuple(int(c * 255) for c in colorsys.hsv_to_rgb(((i / KEYS) + t * 0.2) % 1.0, 0.8, br)) for i in range(KEYS)]
        if n == 'windmill':
            # 彩虹风车：以键盘中心为轴，色相随角度分布并整体旋转
            cx, cy = 11.25, 3.0
            spin = t * (0.12 + sp * 0.35)
            out = []
            for i in range(KEYS):
                x, y = self.xy[i]
                ang = math.atan2(y - cy, x - cx) / (2 * math.pi)
                hue = (ang + spin) % 1.0
                # 叶片感：亮度随角度做三次谐波，像三片风车
                blade = 0.55 + 0.45 * math.cos(ang * 2 * math.pi * 3)
                rr, gg, bb = colorsys.hsv_to_rgb(hue, 1.0, br * blade)
                out.append((int(rr * 255), int(gg * 255), int(bb * 255)))
            return out
        return [(int(r * br), int(g * br), int(b * br))] * KEYS

    def _loop(self):
        t0 = time.time()
        while self.running:
            t = (time.time() - t0) * (0.5 + self.speed / 6.0)
            self.kb.set_pixels(self._pixels(t))
            time.sleep(1.0 / FPS)


class PreviewThread:
    """后台持续读键盘灯色（20fps），前端只取缓存"""

    def __init__(self, kb):
        self.kb = kb
        self.running = False
        self.colors = [[0, 0, 0] for _ in range(KEYS)]
        self.thread = None

    def start(self):
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False

    def _loop(self):
        while self.running:
            try:
                if self.kb.page in (0xFF68, 0xFF60):
                    self.colors = self._read()
            except Exception:
                pass
            # 无线读一帧要 4 个来回，降频避免和按键/写入抢带宽
            time.sleep(1.0 / 30)   # 预览读色 30fps（有线/无线统一）

    def _read(self):
        cols = self.kb.read_leds()
        return cols if cols else [[0, 0, 0] for _ in range(KEYS)]


class MusicEngine:
    """采集系统输出音频，分频段映射到键盘"""

    def __init__(self, kb):
        self.kb = kb
        self.running = False
        self.bands = 10
        self.smoothed = np.zeros(self.bands)
        self.peak = np.zeros(self.bands)
        self.brightness = 5
        self.gain = 2.2
        self.xy = XY
        self.track = np.ones(self.bands) * 0.01
        self.norm = np.zeros(self.bands)
        self.mode = 'bars'
        self.color = (255, 40, 40)
        self.band_weight = np.array([0.22, 0.25, 0.30, 0.34, 0.39, 0.45, 0.52, 0.59, 0.66, 0.73])
        self.thread = None

    def start(self):
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False

    def _loop(self):
        t0 = time.time()
        try:
            speaker = sc.default_speaker()
            mic = sc.get_microphone(speaker.name, include_loopback=True)
            with mic.recorder(samplerate=48000, channels=2, blocksize=1024) as rec:
                win = np.hanning(1024)
                freqs = np.fft.rfftfreq(1024, 1 / 48000)
                edges = np.geomspace(40, 16000, self.bands + 1)
                masks = [(freqs >= edges[i]) & (freqs < edges[i + 1]) for i in range(self.bands)]
                while self.running:
                    data = rec.record(numframes=1024)
                    mono = data[:, 0] if data.ndim > 1 else data
                    spec = np.abs(np.fft.rfft(mono * win)) / (len(mono) / 2.0)
                    # 频段 RMS（仿网页 q7）
                    levels = np.array([float(np.sqrt(np.mean(spec[m] ** 2))) if m.any() else 0.0 for m in masks])
                    # 自适应增益 AGC（仿网页 J7：上升 0.3 / 下降 0.008 / 最小 0.01 / 阈值 5）
                    for bi in range(self.bands):
                        e = float(levels[bi])
                        prev = float(self.track[bi])
                        rate = 0.3 if e > prev else 0.008
                        peak = max(prev * (1 - rate) + e * rate, 0.01)
                        self.track[bi] = peak
                        v = e / peak
                        self.norm[bi] = 0.0 if (v * 1000.0) <= 5.0 else min(1.0, v)
                    br = max(0.1, self.brightness / 6.0)
                    pixels = [(0, 0, 0)] * KEYS
                    mode = self.mode
                    tnow = time.time() - t0
                    if mode == 'bars':
                        # 柱状频谱：底排起按该列电平点亮，柱越高越暗，像均衡器
                        for i in range(KEYS):
                            x, y = self.xy[i]
                            band = int(min(self.bands - 1, max(0, x / 22.5 * self.bands)))
                            lvl = float(self.norm[band])
                            row_from_bottom = 5.0 - y
                            h = lvl * 6.0
                            if row_from_bottom < h:
                                fade = 0.55 + 0.45 * (1.0 - row_from_bottom / max(h, 0.9))
                                v = min(1.0, br * self.gain / 2.2 * fade)
                                r, g, b = colorsys.hsv_to_rgb(band / self.bands, 1.0, 1.0)
                                pixels[i] = (int(r * 255 * v), int(g * 255 * v), int(b * 255 * v))
                    elif mode == 'volcano':
                        # 块状火山：左中右三段，红/绿/蓝三色块
                        for i in range(KEYS):
                            x, y = self.xy[i]
                            seg = 0 if x < 7.5 else (1 if x < 15.0 else 2)
                            band = int(min(self.bands - 1, max(0, x / 22.5 * self.bands)))
                            lvl = float(self.norm[band])
                            rowf = 0.65 + min(0.35, (y / 5.0) * 0.35)
                            v = min(1.0, lvl * br * rowf * 1.8 * self.gain / 2.2)
                            r, g, b = colorsys.hsv_to_rgb(seg / 3.0, 1.0, 1.0)
                            pixels[i] = (int(r * 255 * v), int(g * 255 * v), int(b * 255 * v))
                    elif mode == 'rainbow':
                        for i in range(KEYS):
                            x, y = self.xy[i]
                            band = int(min(self.bands - 1, max(0, x / 22.5 * self.bands)))
                            lvl = float(self.norm[band])
                            hue = (x / 22.5 + tnow * 0.15) % 1.0
                            v = min(1.0, (0.25 + 0.75 * lvl) * br * self.gain / 2.2)
                            r, g, b = colorsys.hsv_to_rgb(hue, 1.0, 1.0)
                            pixels[i] = (int(r * 255 * v), int(g * 255 * v), int(b * 255 * v))
                    elif mode == 'pulse':
                        # RGB 脉冲：整块彩虹渐变，整体亮度跟着音量一起起伏
                        avg = float(np.mean(self.norm))
                        v = min(1.0, (avg ** 0.6) * br * 2.2 * self.gain / 2.2)
                        hue0 = (tnow * 0.10) % 1.0
                        for i in range(KEYS):
                            x, y = self.xy[i]
                            hue = (hue0 + x / 22.5) % 1.0
                            r, g, b = colorsys.hsv_to_rgb(hue, 1.0, 1.0)
                            pixels[i] = (int(r * 255 * v), int(g * 255 * v), int(b * 255 * v))
                    self.kb.set_pixels(pixels[:KEYS], max_chunks=10)
                    time.sleep(1.0 / FPS)
        except Exception as e:
            print("music error:", e)
            self.running = False



# ---------------- 按键监听（Windows 全局低级钩子） ----------------
import ctypes
import ctypes.wintypes as _wt

WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100

_VK_TO_HID = {}
for _i in range(26):
    _VK_TO_HID[0x41 + _i] = 4 + _i          # A-Z
for _i in range(1, 10):
    _VK_TO_HID[0x30 + _i] = 29 + _i         # 1-9
_VK_TO_HID[0x30] = 39                        # 0
_VK_TO_HID[0x20] = 44                        # space
_VK_TO_HID[0x0D] = 40                        # enter
_VK_TO_HID[0x1B] = 41                        # esc
_VK_TO_HID[0x08] = 42                        # backspace
_VK_TO_HID[0x09] = 43                        # tab
_VK_TO_HID[0x25] = 80                        # left
_VK_TO_HID[0x26] = 82                        # up
_VK_TO_HID[0x27] = 79                        # right
_VK_TO_HID[0x28] = 81                        # down
_VK_TO_HID[0x10] = 225                       # shift
_VK_TO_HID[0x11] = 224                       # ctrl
_VK_TO_HID[0x12] = 226                       # alt
_VK_TO_HID[0xBA] = 51                        # ;
_VK_TO_HID[0xBB] = 48                        # =
_VK_TO_HID[0xBC] = 54                        # ,
_VK_TO_HID[0xBD] = 45                        # -
_VK_TO_HID[0xBE] = 55                        # .
_VK_TO_HID[0xBF] = 56                        # /
_VK_TO_HID[0xC0] = 53                        # `
_VK_TO_HID[0xDB] = 47                        # [
_VK_TO_HID[0xDC] = 49                        # backslash
_VK_TO_HID[0xDD] = 48                        # ]
_VK_TO_HID[0xDE] = 52                        # '

_HID_TO_LED = {}
for _row in LAYOUT.get("rows", []):
    for _k in _row:
        _HID_TO_LED.setdefault(int(_k.get("keyCode", -1)), _k.get("value", -1))


class KeyListener:
    """全局键盘钩子，只旁听（不阻断输入），按键时回调 on_key(led_index)"""

    def __init__(self, on_key):
        self.on_key = on_key
        self.running = False
        self.thread = None
        self._proc = None
        self._hook = None

    def start(self):
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False

    def _loop(self):
        user32 = ctypes.WinDLL('user32', use_last_error=True)
        user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, _wt.WPARAM, ctypes.c_ssize_t]
        user32.CallNextHookEx.restype = ctypes.c_ssize_t
        HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_int, _wt.WPARAM, ctypes.c_ssize_t)

        def handler(nCode, wParam, lParam):
            if nCode == 0 and wParam == WM_KEYDOWN:
                try:
                    vk = ctypes.cast(int(lParam), ctypes.POINTER(_wt.DWORD))[0]
                    hid_code = _VK_TO_HID.get(int(vk))
                    if hid_code is not None:
                        led = _HID_TO_LED.get(hid_code)
                        if led is not None and led >= 0:
                            self.on_key(led)
                except Exception:
                    pass
            return user32.CallNextHookEx(None, nCode, wParam, int(lParam))

        self._proc = HOOKPROC(handler)
        self._hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc, None, 0)
        msg = _wt.MSG()
        while self.running:
            if user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
            else:
                time.sleep(0.003)
        if self._hook:
            user32.UnhookWindowsHookEx(self._hook)


class RippleEngine:
    """按键涟漪：亮起后逐渐衰减"""

    def __init__(self, kb, fps=30, decay=2.6):
        self.kb = kb
        self.running = False
        self.fps = fps
        self.decay = decay
        self.hits = {}          # led -> 亮度
        self.thread = None
        self.color = (60, 180, 255)

    def start(self):
        if not self.running:
            self.running = True
            self.thread = threading.Thread(target=self._loop, daemon=True)
            self.thread.start()

    def stop(self):
        self.running = False

    def hit(self, led):
        self.hits[led] = 1.0

    def _loop(self):
        last = time.time()
        while self.running:
            now = time.time()
            dt = min(0.1, now - last)
            last = now
            for led in list(self.hits.keys()):
                self.hits[led] -= self.decay * dt
                if self.hits[led] <= 0:
                    del self.hits[led]
            pixels = [(0, 0, 0)] * KEYS
            r, g, b = self.color
            for led, v in self.hits.items():
                if 0 <= led < KEYS:
                    v = max(0.0, min(1.0, v))
                    pixels[led] = (int(r * v), int(g * v), int(b * v))
            self.kb.set_pixels(pixels)
            time.sleep(1.0 / self.fps)


class Api:
    def __init__(self):
        self.kb = Keyboard()
        self.fx = EffectEngine(self.kb)
        self.music = MusicEngine(self.kb)
        self.preview = PreviewThread(self.kb)
        self.ripple = RippleEngine(self.kb)
        self.key_listener = KeyListener(self.ripple.hit)
        self.connected = self.kb.open()
        self.fw_mode = None
        self.music_prev = None
        self.realtime = True
        self.paint_colors = {}
        self.color_mode = 1
        self.direction = 3
        self._apply_saved_state()

    # ---------- 记住退出时的选择，下次启动强制恢复 ----------
    def _save_state(self):
        try:
            data = {
                'mode': self.fw_mode,
                'color': list(self.fx.color),
                'brightness': self.fx.brightness,
                'speed': self.fx.speed,
                'color_mode': self.color_mode,
                'direction': self.direction,
            }
            with open(STATE_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f)
        except Exception:
            pass

    def _apply_saved_state(self):
        if not self.connected:
            return
        try:
            with open(STATE_FILE, 'r', encoding='utf-8') as f:
                d = json.load(f)
        except Exception:
            return
        try:
            mode = int(d.get('mode') or 0)
            if not (1 <= mode <= 19):
                return
            c = d.get('color') or [255, 255, 255]
            self.fx.color = (int(c[0]), int(c[1]), int(c[2]))
            self.fx.brightness = max(1, min(6, int(d.get('brightness', 5))))
            self.fx.speed = max(0, min(6, int(d.get('speed', 3))))
            self.color_mode = 1 if int(d.get('color_mode', 1)) else 0
            self.direction = int(d.get('direction', 3))
            self.fw_mode = mode
            self.kb.set_mode(mode, self.fx.brightness, self.fx.speed,
                             self.fx.color, self.color_mode, self.direction)
            print('RESTORE mode=', mode, 'color=', self.fx.color, flush=True)
        except Exception:
            pass

    def window_min(self):
        try:
            self.window.minimize()
        except Exception:
            pass
        return {'ok': True}

    def window_max(self):
        try:
            if getattr(self, '_maxed', False):
                self.window.restore()
                self._maxed = False
            else:
                self.window.maximize()
                self._maxed = True
        except Exception:
            pass
        return {'ok': True}

    def window_close(self):
        try:
            self._close_cb()
        except Exception:
            pass
        return {'ok': True}

    def status(self):
        print('STATUS page=', hex(self.kb.page) if self.kb.page else None, flush=True)
        return {
            'connected': self.connected,
            'page': hex(self.kb.page) if self.kb.page else None,
            'effect': self.fx.name,
            'fwMode': self.fw_mode,
            'color': '#%02x%02x%02x' % self.fx.color,
            'brightness': self.fx.brightness,
            'speed': self.fx.speed,
            'colorMode': self.color_mode,
            'direction': self.direction,
            'layout': LAYOUT,
        }

    def connect(self):
        self.connected = self.kb.open()
        return {'connected': self.connected}

    def set_effect(self, name):
        self.fw_mode = None
        self.paint_colors = {}
        self.music.stop()
        self.fx.start(name)
        return {'ok': True, 'effect': name}

    def music_start(self):
        self.fx.stop()
        # 记下开启前的灯效，关掉音乐律动时还原回去
        prev_fw = self.fw_mode
        if prev_fw is not None and not (1 <= int(prev_fw) <= 19):
            prev_fw = 1          # 非法模式（如残废的 20）退回静态恒亮，至少全亮
        self.music_prev = {
            'fw_mode': prev_fw,
            'fx_name': self.fx.name,
            'color': self.fx.color,
            'brightness': self.fx.brightness,
            'speed': self.fx.speed,
            'color_mode': self.color_mode,
            'direction': self.direction,
        }
        self.fw_mode = None
        time.sleep(0.1)
        self.kb.set_mode(128, self.fx.brightness, self.fx.speed, self.fx.color)
        self.music.brightness = self.fx.brightness
        self.music.start()
        return {'ok': True}

    def music_stop(self):
        self.music.stop()
        # 等音频线程真正退出，否则它最后一帧 set_pixels 会把刚下发的固件效果顶掉
        t = getattr(self.music, 'thread', None)
        if t and t.is_alive():
            t.join(timeout=1.5)
        p = getattr(self, 'music_prev', None)
        print('MUSIC_STOP prev=', p, flush=True)
        if p:
            # 还原参数再重新下发灯效
            self.fx.color = p['color']
            self.fx.brightness = p['brightness']
            self.fx.speed = p['speed']
            self.color_mode = p['color_mode']
            self.direction = p['direction']
            self.music_prev = None
            time.sleep(0.6)
            if p['fw_mode'] is not None:
                self.set_firmware_mode(p['fw_mode'])
            else:
                self.fx.start(p['fx_name'])
        return {'ok': True}

    def set_music_gain(self, v):
        self.music.gain = max(0.5, min(4.0, float(v)))
        return {'gain': self.music.gain}

    def ripple_start(self):
        self.fx.stop()
        self.music.stop()
        self.fw_mode = None
        time.sleep(0.1)
        self.kb.set_mode(128, self.fx.brightness, self.fx.speed, self.fx.color)
        self.ripple.start()
        self.key_listener.start()
        return {'ok': True}

    def ripple_stop(self):
        self.ripple.stop()
        self.key_listener.stop()
        return {'ok': True}

    def preview_start(self):
        self.preview.start()
        return {'ok': True}

    def set_realtime(self, on):
        """实时读取开关：关掉后不再后台读键盘灯色（无线模式读会阻塞）"""
        self.realtime = bool(on)
        if self.realtime:
            self.preview.start()
        else:
            self.preview.stop()
        return {'ok': True, 'realtime': self.realtime}

    def led_colors(self):
        """音乐律动时返回软件推送的颜色（本地渲染，流畅）；否则返回后台读取的真实颜色"""
        if self.music.running:
            px = self.kb.last_pixels
            if px:
                return {'ok': True, 'colors': [list(c) for c in px]}
        if self.kb.page not in (0xFF68, 0xFF60):
            return {'ok': False, 'reason': 'no_device'}
        return {'ok': True, 'colors': self.preview.colors}

    def current_effect(self):
        """读键盘当前灯效（GET_LED_EFFECT=19）。数据区（帧头 +8 起）是
        mode,r,g,b,driverSetting,secR,secG,secB,colorMode,brightness,speed,direction。
        无线通道读响应会长时间超时，直接不读。"""
        if self.kb.page != 0xFF68:
            return {'ok': False, 'reason': 'wired_only'}
        try:
            with self.kb.io_lock:
                self.kb._request(19, 24, 0)
                for _ in range(6):
                    r = self.kb._read_match(19, None, tries=3)
                    if not r:
                        break
                    base = 0 if r[0] == 0x55 else 1
                    d = r[base + 8:]
                    if len(d) < 12:
                        continue
                    for i in range(0, 5):
                        if i + 4 >= len(d):
                            break
                        if 1 <= d[i] <= 19 and (d[i + 1] or d[i + 2] or d[i + 3]):
                            # 把读到的模式记成当前固件模式，否则启动后 fw_mode 是 None，
                            # 改颜色/颜色模式时 _refresh_fw 不会重发，看起来「没反应」
                            self.fw_mode = int(d[i])
                            return {
                                'ok': True,
                                'mode': int(d[i]),
                                'color': [int(d[i + 1]), int(d[i + 2]), int(d[i + 3])],
                                'colorMode': int(d[i + 8]) if i + 8 < len(d) else 1,
                                'brightness': int(d[i + 9]) if i + 9 < len(d) else 5,
                                'speed': int(d[i + 10]) if i + 10 < len(d) else 3,
                                'direction': int(d[i + 11]) if i + 11 < len(d) else 3,
                            }
            return {'ok': False}
        except Exception:
            return {'ok': False}

    def set_color_mode(self, v):
        self.color_mode = 1 if int(v) else 0
        self._refresh_fw()
        self._save_state()
        return {'color_mode': self.color_mode}

    def set_direction(self, v):
        self.direction = 3 if int(v) else 4
        self._refresh_fw()
        self._save_state()
        return {'direction': self.direction}

    def set_music_mode(self, m):
        if m in ('bars', 'volcano', 'rainbow', 'pulse'):
            self.music.mode = m
        return {'mode': self.music.mode}

    RGB_MODES = {4, 5, 6, 8, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20}
    FORCE_RGB_MODES = {6, 8}   # 百花争艳、光谱循环只能 RGB，单色无效 → 强制 RGB

    def set_firmware_mode(self, mode):
        """使用键盘固件自带灯效（流畅、零带宽）"""
        self.music.stop()
        self.paint_colors = {}
        self.fx.stop()
        time.sleep(0.15)
        self.fw_mode = int(mode)
        self.kb.last_pixels = None
        # RGB/动效类模式不吃基础色，传了反而盖掉它自己的彩虹
        color = (255, 255, 255) if int(mode) in self.RGB_MODES else self.fx.color
        cm = 1 if int(mode) in self.FORCE_RGB_MODES else self.color_mode
        self.color_mode = cm
        self.kb.set_mode(int(mode), self.fx.brightness, self.fx.speed, color, cm, self.direction)
        self._save_state()
        return {'ok': True, 'mode': int(mode)}

    def _refresh_fw(self):
        if self.fw_mode is not None:
            color = (255, 255, 255) if int(self.fw_mode) in self.RGB_MODES else self.fx.color
            self.kb.set_mode(self.fw_mode, self.fx.brightness, self.fx.speed, color, self.color_mode, self.direction)

    def set_color(self, hex_color):
        h = hex_color.lstrip('#')
        self.fx.color = (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
        self.music.color = self.fx.color
        if self.fw_mode is not None:
            self._refresh_fw()
        elif self.kb.page != 0xFF68:
            # 无线启动时读不到当前模式，退回静态恒亮（至少颜色能生效）
            self.set_firmware_mode(1)
        else:
            self.fx.start(self.fx.name)
        self._save_state()
        return {'ok': True}

    def set_brightness(self, v):
        self.fx.brightness = max(0, min(6, int(v)))
        self.music.brightness = self.fx.brightness
        self._refresh_fw()
        if self.paint_colors:
            self.paint(self.paint_colors)
        self._save_state()
        return {'brightness': self.fx.brightness}

    def set_speed(self, v):
        self.fx.speed = max(0, min(6, int(v)))
        self._refresh_fw()
        self._save_state()
        return {'speed': self.fx.speed}

    def layout(self):
        return LAYOUT

    def saved_paint(self):
        return load_paint()

    def battery(self):
        """读电量：有线用 cmd 16，无线用 cmd 59 响应里的电量字节。"""
        if self.kb.page == 0xFF60:
            lv = self.kb.battery_24g()
            if lv is None:
                return {'ok': False, 'reason': 'wired_only'}
            return {'ok': True, 'level': max(0, min(100, lv)), 'charging': False, 'wireless': True}
        if self.kb.page != 0xFF68:
            return {'ok': False, 'reason': 'wired_only'}
        try:
            # 独占 IO 即可，不用停预览线程（停线程反而可能卡在它持有的锁上）
            with self.kb.io_lock:
                for _ in range(5):
                    self.kb._request(16, 24, 0)
                    r = self.kb._read_match(16, None, tries=4)
                    if not r:
                        continue
                    base = 0 if r[0] == 0x55 else 1
                    if len(r) <= base + 26:
                        continue
                    level = r[base + 25]
                    charging = r[base + 26]
                    if 0 <= level <= 100:
                        return {'ok': True, 'level': level, 'charging': 1 if charging else 0}
            return {'ok': False}
        except Exception as e:
            print('BATTERY err:', repr(e), flush=True)
            return {'ok': False}

    def paint(self, colors):
        """画板：colors = {"LED编号": [r,g,b], ...}，按当前亮度缩放"""
        self.paint_colors = dict(colors or {})
        br = max(0.0, min(1.0, self.fx.brightness / 6.0))
        pixels = [(0, 0, 0)] * KEYS
        for k, v in self.paint_colors.items():
            try:
                i = int(k)
                if 0 <= i < KEYS:
                    pixels[i] = (int(v[0] * br), int(v[1] * br), int(v[2] * br))
            except Exception:
                continue
        self.fx.stop()
        self.music.stop()
        time.sleep(0.1)
        self.fw_mode = None
        self.kb.set_mode(128, self.fx.brightness, self.fx.speed, self.fx.color)
        self.kb.set_pixels(pixels, force=True)
        save_paint(self.paint_colors)
        return {'ok': True, 'count': len(self.paint_colors)}

    def stop(self):
        self.fx.stop()
        self.music.stop()
        self.paint_colors = {}
        time.sleep(0.15)
        self.fw_mode = None
        self.kb.set_mode(1, self.fx.brightness, self.fx.speed, self.fx.color)
        return {'ok': True}


AUTOSTART_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
AUTOSTART_NAME = 'DMLit'


def autostart_command():
    """开机启动要执行的命令行（源码运行 / 打包 exe 都兼容）"""
    here = os.path.dirname(os.path.abspath(__file__))
    if getattr(sys, 'frozen', False):
        return '"%s"' % sys.executable
    return '"%s" "%s"' % (sys.executable, os.path.join(here, 'app.py'))


def autostart_enabled():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_KEY) as k:
            winreg.QueryValueEx(k, AUTOSTART_NAME)
            return True
    except Exception:
        return False


def set_autostart(enable):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_KEY, 0, winreg.KEY_SET_VALUE) as k:
            if enable:
                winreg.SetValueEx(k, AUTOSTART_NAME, 0, winreg.REG_SZ, autostart_command())
            else:
                try:
                    winreg.DeleteValue(k, AUTOSTART_NAME)
                except FileNotFoundError:
                    pass
        return True
    except Exception:
        return False


def tray_image():
    """托盘图标：优先用 splash.png 缩一个 64px 的圆图标"""
    from PIL import Image
    here = os.path.dirname(os.path.abspath(__file__))
    for name in ('splash.png', 'splash.jpg'):
        path = os.path.join(here, name)
        if os.path.exists(path):
            try:
                im = Image.open(path).convert('RGBA')
                im.thumbnail((64, 64), Image.LANCZOS)
                return im
            except Exception:
                pass
    return Image.new('RGBA', (64, 64), (43, 92, 255, 255))


def main():
    api = Api()
    here = os.path.dirname(os.path.abspath(__file__))
    window = webview.create_window(
        'DMLit · VEGA108',
        os.path.join(here, 'index.html'),
        js_api=api, width=1112, height=948, min_size=(1112, 948), background_color='#f4f6f9',
        frameless=True, easy_drag=True,
    )
    api.window = window

    quitting = {'value': False}
    hidden_state = {'realtime': True}

    def do_show(*_):
        try:
            window.show()
            window.restore()
        except Exception:
            pass
        # 窗口回来了，恢复隐藏前的实时读取状态
        try:
            api.set_realtime(hidden_state['realtime'])
        except Exception:
            pass

    def do_quit(*_):
        quitting['value'] = True
        try:
            icon.stop()
        except Exception:
            pass
        try:
            window.destroy()
        except Exception:
            pass
        os._exit(0)

    def toggle_autostart(*_):
        set_autostart(not autostart_enabled())

    # 关闭窗口 = 收进托盘，不退出（退出走托盘菜单）
    def on_closing():
        if quitting['value']:
            return True
        # 收进托盘时停掉实时读取（省键盘带宽/CPU），窗口回来再恢复
        try:
            hidden_state['realtime'] = api.realtime
            api.set_realtime(False)
        except Exception:
            pass
        try:
            window.hide()
        except Exception:
            pass
        return False

    try:
        window.events.closing += on_closing
    except Exception:
        pass
    # 前端自定义标题栏的关闭按钮走这里（收托盘）
    api._close_cb = on_closing

    # 默认开启开机自启（用户选择）
    if not autostart_enabled():
        set_autostart(True)

    import pystray
    menu = pystray.Menu(
        pystray.MenuItem('显示主窗口', do_show, default=True),
        pystray.MenuItem('开机自启', toggle_autostart,
                         checked=lambda item: autostart_enabled()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem('退出 DMLit', do_quit),
    )
    icon = pystray.Icon('DMLit', tray_image(), 'DMLit · VEGA108', menu)
    threading.Thread(target=icon.run, daemon=True).start()

    webview.start()


if __name__ == '__main__':
    main()
