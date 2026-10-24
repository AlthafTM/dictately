from _support import ROOT
"""Owned native editors for Rust destination-routing regression tests. No external app input."""
import ctypes as c
from ctypes import wintypes as w
import json, queue, sys, threading, time
u = c.WinDLL('user32')
u.CreateWindowExW.argtypes = [w.DWORD,w.LPCWSTR,w.LPCWSTR,w.DWORD,c.c_int,c.c_int,c.c_int,c.c_int,w.HWND,w.HMENU,w.HINSTANCE,c.c_void_p]
u.CreateWindowExW.restype = w.HWND
u.SetFocus.argtypes = [w.HWND]
u.SetForegroundWindow.argtypes = [w.HWND]
u.GetForegroundWindow.restype = w.HWND
u.SendMessageW.argtypes = [w.HWND,w.UINT,w.WPARAM,w.LPARAM]
u.GetWindowTextW.argtypes = [w.HWND,w.LPWSTR,c.c_int]
u.DestroyWindow.argtypes = [w.HWND]
a = u.CreateWindowExW(0,'STATIC','Dictately input routing QA',0x10CF0000,100,100,600,300,None,None,None,None)
b = u.CreateWindowExW(0,'STATIC','Dictately other destination QA',0x10CF0000,120,450,600,200,None,None,None,None)
style=0x50000000|(0x44 if '--multiline' in sys.argv else 0)
fields = [u.CreateWindowExW(0,'EDIT','Existing: ',style,20,20+i*65,500,50,a,None,None,None) for i in range(2)]
fields += [u.CreateWindowExW(0,'EDIT','Other: ',style,20,20,500,50,b,None,None,None)]
commands = queue.Queue()
def reader():
    for line in sys.stdin: commands.put(line.strip())
threading.Thread(target=reader,daemon=True).start()
print(json.dumps({'window':a,'other':b}),flush=True)
msg = w.MSG()
try:
    while True:
        while u.PeekMessageW(c.byref(msg),None,0,0,1):
            u.TranslateMessage(c.byref(msg));u.DispatchMessageW(c.byref(msg))
        try: cmd=commands.get_nowait()
        except queue.Empty: time.sleep(.005);continue
        if cmd=='quit': break
        if cmd=='home':
            u.SendMessageW(fields[0],0xB1,0,0)
            print('{}',flush=True)
        if cmd.startswith('select '):
            start,end=map(int,cmd.split()[1:]);u.SendMessageW(fields[0],0xB1,start,end)
            print('{}',flush=True)
        if cmd=='selection':
            start=w.DWORD();end=w.DWORD()
            u.SendMessageW(fields[0],0xB0,c.cast(c.byref(start),c.c_void_p).value,c.cast(c.byref(end),c.c_void_p).value)
            print(json.dumps([start.value,end.value]),flush=True)
        if cmd.startswith('focus '):
            i=int(cmd.split()[1]); root=a if i<2 else b
            u.SetForegroundWindow(root);u.SetFocus(fields[i]);u.SendMessageW(fields[i],0xB1,4096,4096)
            print(json.dumps({'focused':u.GetForegroundWindow()==root}),flush=True)
        elif cmd=='read':
            texts=[]
            for field in fields:
                buf=c.create_unicode_buffer(4096);u.GetWindowTextW(field,buf,4096);texts.append(buf.value)
            print(json.dumps(texts),flush=True)
finally:
    u.DestroyWindow(a);u.DestroyWindow(b)
