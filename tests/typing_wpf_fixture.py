"""Compile the owned WPF fixture using the installed Windows .NET Framework."""
from _support import ROOT
from pathlib import Path
import hashlib, os, subprocess
source=ROOT/'tests/typing_wpf_fixture.cs'
framework=Path(os.environ['WINDIR'])/'Microsoft.NET/Framework64/v4.0.30319'
folder=ROOT/'qa'/('wpf-fixture-'+hashlib.sha256(source.read_bytes()).hexdigest()[:12])
folder.mkdir(parents=True,exist_ok=True)
exe=folder/'fixture.exe'
if not exe.exists():
    references=[framework/'WPF'/name for name in ['PresentationCore.dll','PresentationFramework.dll','WindowsBase.dll']]
    references += [framework/'System.Xaml.dll',framework/'System.Web.Extensions.dll']
    result=subprocess.run([str(framework/'csc.exe'),'/nologo','/target:exe','/out:'+str(exe),*['/r:'+str(p) for p in references],str(source)],capture_output=True)
    if result.returncode:raise RuntimeError(result.stdout.decode(errors='replace'))
raise SystemExit(subprocess.call([str(exe)]))
