"""Windows control helpers for owned fixtures."""
from _support import ROOT
import ctypes as c
from ctypes import wintypes as w
import json,os,subprocess,time,uuid
from pathlib import Path

u=c.WinDLL('user32')
u.CreateWindowExW.argtypes=[w.DWORD,w.LPCWSTR,w.LPCWSTR,w.DWORD,c.c_int,c.c_int,c.c_int,c.c_int,w.HWND,w.HMENU,w.HINSTANCE,c.c_void_p];u.CreateWindowExW.restype=w.HWND
u.SetForegroundWindow.argtypes=[w.HWND];u.SetFocus.argtypes=[w.HWND];u.GetForegroundWindow.restype=w.HWND
u.DestroyWindow.argtypes=[w.HWND];u.PostMessageW.argtypes=[w.HWND,w.UINT,w.WPARAM,w.LPARAM]
u.IsWindowVisible.argtypes=[w.HWND];u.GetWindowTextW.argtypes=[w.HWND,w.LPWSTR,c.c_int]
u.keybd_event.argtypes=[w.BYTE,w.BYTE,w.DWORD,c.c_size_t]

def pump():
    msg=w.MSG()
    while u.PeekMessageW(c.byref(msg),None,0,0,1):
        if msg.message in (0x100,0x101,0x104,0x105) and msg.wParam in (18,121):continue
        if msg.message==0x112 and msg.wParam&0xfff0==0xf100:continue
        u.TranslateMessage(c.byref(msg));u.DispatchMessageW(c.byref(msg))

def chord(key,ctrl=False):
    if ctrl:u.keybd_event(17,0,0,0)
    u.keybd_event(key,u.MapVirtualKeyW(key,0),0,0);u.keybd_event(key,u.MapVirtualKeyW(key,0),2,0)
    if ctrl:u.keybd_event(17,0,2,0)
