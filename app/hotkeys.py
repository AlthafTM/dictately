"""Windows shortcut encoding shared by settings and the native controller.

Low byte: Windows virtual key; next nibble: Ctrl=1 Shift=2 Alt=4 Win=8.
"""
import ctypes
import os

DICTATION_DEFAULT=0x7B
LANGUAGE_DEFAULT=0x79
MODIFIERS={0x10,0x11,0x12,0x5B,0x5C,0xA0,0xA1,0xA2,0xA3,0xA4,0xA5}
CAPTURE_EVENT='Local\\Dictately.ShortcutCapture'


def valid_shortcut(value):
    return type(value) is int and 0<value<=0xFFF and 8<=value&255<=254 and value&255 not in MODIFIERS|{0x1B}


def shortcut_name(value):
    if not valid_shortcut(value):return 'Unassigned'
    key=value&255
    prefix=[name for bit,name in [(1,'Ctrl'),(2,'Shift'),(4,'Alt'),(8,'Win')] if (value>>8)&bit]
    special={8:'Backspace',9:'Tab',13:'Enter',19:'Pause',20:'Caps Lock',32:'Space',33:'Page Up',34:'Page Down',35:'End',36:'Home',37:'Left',38:'Up',39:'Right',40:'Down',44:'Print Screen',45:'Insert',46:'Delete'}
    name=f'F{key-111}' if 112<=key<=135 else chr(key) if 48<=key<=90 else special.get(key)
    if name is None and os.name=='nt':
        user=ctypes.windll.user32;scan=user.MapVirtualKeyW(key,0)
        text=ctypes.create_unicode_buffer(80);user.GetKeyNameTextW(scan<<16,text,80)
        name=text.value
    return '+'.join(prefix+[name or f'Key {key}'])


def capture_shortcut(parent,current,on_chosen):
    """Suspend controller shortcuts only while this process owns the capture event."""
    import tkinter as tk
    from tkinter import ttk
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateEventW.argtypes=[ctypes.c_void_p,ctypes.c_int,ctypes.c_int,ctypes.c_wchar_p]
    kernel.CreateEventW.restype=ctypes.c_void_p
    kernel.CloseHandle.argtypes=[ctypes.c_void_p]
    handle=kernel.CreateEventW(None,1,1,CAPTURE_EVENT)
    dialog=tk.Toplevel(parent);dialog.title('Assign shortcut');dialog.configure(bg='#111111');dialog.transient(parent)
    dialog.geometry(f'380x150+{parent.winfo_rootx()+70}+{parent.winfo_rooty()+100}');dialog.resizable(False,False)
    ttk.Label(dialog,text='Press a key or key combination',font=('Segoe UI',13,'bold')).pack(pady=(25,8))
    ttk.Label(dialog,text='Esc cancels · Modifier keys can be combined').pack()
    def close():
        if handle:kernel.CloseHandle(handle)
        dialog.destroy()
    def pressed(event):
        if event.keycode==27:close();return 'break'
        if event.keycode in MODIFIERS:return 'break'
        user=ctypes.windll.user32
        mods=sum(bit for vk,bit in [(0x11,1),(0x10,2),(0x12,4)] if user.GetAsyncKeyState(vk)&0x8000)
        if user.GetAsyncKeyState(0x5B)&0x8000 or user.GetAsyncKeyState(0x5C)&0x8000:mods|=8
        value=(mods<<8)|event.keycode
        if valid_shortcut(value):on_chosen(value);close()
        return 'break'
    dialog.bind('<KeyPress>',pressed);dialog.protocol('WM_DELETE_WINDOW',close)
    dialog.grab_set();dialog.focus_force()
