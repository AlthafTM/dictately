"""Compare executable icon resources with every size in the current ICO."""
from _support import ROOT
import ctypes as c
import glob
from pathlib import Path
import struct
import sys


def check(path):
    source=(ROOT/'assets/dictately.ico').read_bytes()
    _,kind,count=struct.unpack_from('<HHH',source)
    assert kind==1 and count==8
    expected=set()
    for index in range(count):
        length,offset=struct.unpack_from('<II',source,6+16*index+8)
        expected.add(source[offset:offset+length])
    kernel=c.WinDLL('kernel32',use_last_error=True)
    kernel.LoadLibraryExW.argtypes=[c.c_wchar_p,c.c_void_p,c.c_uint]
    kernel.LoadLibraryExW.restype=c.c_void_p
    kernel.FindResourceW.argtypes=[c.c_void_p,c.c_void_p,c.c_void_p]
    kernel.FindResourceW.restype=c.c_void_p
    kernel.SizeofResource.argtypes=[c.c_void_p,c.c_void_p]
    kernel.LoadResource.argtypes=[c.c_void_p,c.c_void_p]
    kernel.LoadResource.restype=c.c_void_p
    kernel.LockResource.argtypes=[c.c_void_p];kernel.LockResource.restype=c.c_void_p
    kernel.FreeLibrary.argtypes=[c.c_void_p]
    callback=c.WINFUNCTYPE(c.c_int,c.c_void_p,c.c_void_p,c.c_void_p,c.c_ssize_t)
    kernel.EnumResourceNamesW.argtypes=[c.c_void_p,c.c_void_p,callback,c.c_ssize_t]
    module=kernel.LoadLibraryExW(str(path.resolve()),None,0x22)
    assert module,(path,c.get_last_error())
    names=[]
    @callback
    def collect(module,kind,name,param):
        names.append(name if name<=65535 else c.wstring_at(name))
        return 1
    def resource(name,kind):
        pointer=name if isinstance(name,int) else c.cast(c.c_wchar_p(name),c.c_void_p)
        handle=kernel.FindResourceW(module,pointer,kind)
        assert handle,(path,name,kind)
        size=kernel.SizeofResource(module,handle)
        return c.string_at(kernel.LockResource(kernel.LoadResource(module,handle)),size)
    try:
        assert kernel.EnumResourceNamesW(module,14,collect,0)
        for name in names:
            group=resource(name,14);entries=struct.unpack_from('<H',group,4)[0]
            actual={resource(struct.unpack_from('<H',group,6+14*i+12)[0],3) for i in range(entries)}
            if actual==expected:
                print(f'{path}: all {count} embedded icon sizes match')
                return
        raise AssertionError(f'{path}: embedded icon differs from assets/dictately.ico')
    finally:kernel.FreeLibrary(module)


if __name__=='__main__':
    for pattern in sys.argv[1:] or ['native/target/release/dictately.exe']:
        paths=glob.glob(pattern)
        assert paths,f'No icon-check files matched {pattern}'
        for path in paths:check(Path(path))
