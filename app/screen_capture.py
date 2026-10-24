"""Explicit voice commands and native, interactive screenshot launchers."""
from datetime import datetime
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from uuid import uuid4

_COMMANDS=frozenset(('screenshot','screen shot','ss','s s'))

def command_words(text):
    return ' '.join(re.findall(r'[^\W_]+',text.casefold()))

def screenshot_command(text):
    return command_words(text) in _COMMANDS

def screenshot_candidate(text):
    words=command_words(text)
    return bool(words) and any(command==words or command.startswith(words+' ') for command in _COMMANDS)

def capture_command(platform=None,which=None,desktop=None,home=None):
    platform=platform or sys.platform;which=which or shutil.which
    if platform=='win32':
        return [str(Path(os.environ.get('WINDIR',r'C:\Windows'))/'explorer.exe'),'ms-screenclip:']
    if platform=='darwin':
        folder=(Path(home) if home else Path.home())/'Pictures/Dictately'
        folder.mkdir(parents=True,exist_ok=True)
        name=datetime.now().strftime('Screenshot %Y-%m-%d %H-%M-%S')+' '+uuid4().hex[:6]+'.png'
        return ['/usr/sbin/screencapture','-i','-x',str(folder/name)]
    if platform.startswith('linux'):
        desktop=(desktop if desktop is not None else os.environ.get('XDG_CURRENT_DESKTOP','')).lower()
        tools=[('spectacle',['--region']),('gnome-screenshot',['--area','--interactive']),
               ('flameshot',['gui']),('xfce4-screenshooter',['--region'])]
        if 'gnome' in desktop:tools[0],tools[1]=tools[1],tools[0]
        for tool,args in tools:
            executable=which(tool)
            if executable:return [executable,*args]
        raise RuntimeError('No region screenshot tool found. Install Spectacle, GNOME Screenshot, Flameshot, or Xfce Screenshooter')
    raise RuntimeError('Native screenshots are unavailable on this platform')

def launch_screenshot():
    return subprocess.Popen(capture_command(),stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
