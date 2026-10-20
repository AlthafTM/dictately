using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Forms;
using Microsoft.Win32;

[assembly: AssemblyTitle("Dictately Setup")]
[assembly: AssemblyProduct("Dictately")]
[assembly: AssemblyCompany("AlthafTM")]
[assembly: AssemblyVersion("0.10.1.0")]
[assembly: AssemblyFileVersion("0.10.1.0")]

namespace DictatelySetup {
    internal static class Core {
        internal const string Identity = "Dictately:{5BFCCF8A-BAA9-48DF-917C-D1DFE4ABCF88}";
        internal const string LegacyIdentity = "Transcribr:{5BFCCF8A-BAA9-48DF-917C-D1DFE4ABCF88}";
        internal const string LegacyUninstallKey = @"Software\Microsoft\Windows\CurrentVersion\Uninstall\Transcribr";
        internal const string Version = "0.10.1";
        internal const string UninstallKey = @"Software\Microsoft\Windows\CurrentVersion\Uninstall\Dictately";
        internal const string RunKey = @"Software\Microsoft\Windows\CurrentVersion\Run";
        internal static CancellationTokenSource Cancel = new CancellationTokenSource();
        internal static Process Child;
        internal static Action<int, string> Progress = delegate {};
        internal static string Root;
        internal static string Cache;
        internal static bool Startup, Launch = true, RemoveModels = true;
        internal static int DictationKey=123, LanguageKey=121;
        internal static bool BindingsChanged;
        [DllImport("shell32.dll", CharSet=CharSet.Unicode)]
        private static extern void SHChangeNotify(uint events, uint flags, string item1, IntPtr item2);
        internal static bool ValidKey(int value) {
            int key=value&255;
            return value>0 && value<=4095 && key>=8 && key<=254 && key!=27 && key!=91 && key!=92 && !(key>=16&&key<=18) && !(key>=160&&key<=165);
        }
        internal static string KeyLabel(int value) {
            string result="";int modifiers=value>>8;
            if ((modifiers&1)!=0) result+="Ctrl+"; if ((modifiers&2)!=0) result+="Shift+";
            if ((modifiers&4)!=0) result+="Alt+"; if ((modifiers&8)!=0) result+="Win+";
            return result+((Keys)(value&255)).ToString();
        }
        internal static void ReadBindings() {
            try {
                string data=Environment.GetEnvironmentVariable("DICTATELY_DATA") ?? Environment.GetEnvironmentVariable("LOCALFLOW_DATA") ?? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),"Dictately");
                bool legacy=!File.Exists(Path.Combine(data,"settings.json")) && Environment.GetEnvironmentVariable("DICTATELY_DATA")==null && Environment.GetEnvironmentVariable("LOCALFLOW_DATA")==null;
                if (legacy)
                    data=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),"Transcribr");
                string json=File.ReadAllText(Path.Combine(data,"settings.json"));
                var dictation=System.Text.RegularExpressions.Regex.Match(json,"\"dictation_hotkey\"\\s*:\\s*(\\d+)");
                var language=System.Text.RegularExpressions.Regex.Match(json,"\"language_hotkey\"\\s*:\\s*(\\d+)");
                int d=dictation.Success?Int32.Parse(dictation.Groups[1].Value):123;
                int l=language.Success?Int32.Parse(language.Groups[1].Value):121;
                if (legacy && l==122 && d!=121) l=121;
                if (ValidKey(d)&&ValidKey(l)&&d!=l) {DictationKey=d;LanguageKey=l;}
            } catch {}
        }
        internal static string Q(string text) { return "\"" + text.Replace("\"", "\\\"") + "\""; }
        internal static string Normalize(string path) { return Path.GetFullPath(path).TrimEnd(Path.DirectorySeparatorChar); }
        internal static string Marker { get { return Path.Combine(Root, "install-manifest.txt"); } }
        internal static bool IsIdentity(string value) {return value==Identity || value==LegacyIdentity;}
        internal static bool Owned() { return File.Exists(Marker) && IsIdentity(File.ReadLines(Marker).FirstOrDefault()); }
        internal static string Inside(string relative) {
            string path = Path.GetFullPath(Path.Combine(Root, relative));
            if (!path.StartsWith(Root + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase))
                throw new InvalidOperationException("Invalid installation file path");
            string parent = Path.GetDirectoryName(path);
            while (parent != null && parent.StartsWith(Root, StringComparison.OrdinalIgnoreCase)) {
                if (Directory.Exists(parent) && (File.GetAttributes(parent) & FileAttributes.ReparsePoint) != 0)
                    throw new InvalidOperationException("Choose an installation folder without directory links");
                if (parent.Equals(Root, StringComparison.OrdinalIgnoreCase)) break;
                parent = Path.GetDirectoryName(parent);
            }
            if (File.Exists(path) && (File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0)
                throw new InvalidOperationException("Installation files cannot be links");
            return path;
        }
        internal static void Log(string text) {
            lock (typeof(Core)) {
                string log = Owned() ? Inside("setup.log") : Path.Combine(Path.GetTempPath(), "Dictately-setup.log");
                File.AppendAllText(log, DateTime.Now.ToString("s") + " " + text + Environment.NewLine);
            }
        }
        internal static int Run(string file, string arguments, bool capture) {
            Cancel.Token.ThrowIfCancellationRequested();
            ProcessStartInfo info = new ProcessStartInfo(file, arguments) {
                UseShellExecute = false, CreateNoWindow = true, WorkingDirectory = Root,
                RedirectStandardOutput = capture, RedirectStandardError = capture
            };
            info.EnvironmentVariables["DICTATELY_ROOT"] = Root;
            using (Process process = new Process()) {
                process.StartInfo = info;
                if (capture) {
                    DataReceivedEventHandler output = delegate(object sender, DataReceivedEventArgs e) {
                        if (e.Data == null) return;
                        Log(e.Data);
                        string message = "Preparing local speech recognition…";
                        if (e.Data.StartsWith("Downloaded")) message = "Downloading speech model: " + e.Data.Substring(11);
                        else if (e.Data.StartsWith("Verified")) message = "Verifying speech model files…";
                        Progress(-1, message);
                    };
                    process.OutputDataReceived += output; process.ErrorDataReceived += output;
                }
                process.Start(); Child = process;
                if (capture) { process.BeginOutputReadLine(); process.BeginErrorReadLine(); }
                while (!process.WaitForExit(100)) {
                    if (Cancel.IsCancellationRequested) { try { process.Kill(); } catch {} }
                }
                process.WaitForExit(); Child = null;
                Cancel.Token.ThrowIfCancellationRequested();
                return process.ExitCode;
            }
        }
        internal static void Shortcut(string name, string target, string arguments) {
            Type type = Type.GetTypeFromProgID("WScript.Shell");
            object shell = Activator.CreateInstance(type);
            object link = type.InvokeMember("CreateShortcut", BindingFlags.InvokeMethod, null, shell, new object[] {name});
            Type shortcut = link.GetType();
            shortcut.InvokeMember("TargetPath", BindingFlags.SetProperty, null, link, new object[] {target});
            shortcut.InvokeMember("Arguments", BindingFlags.SetProperty, null, link, new object[] {arguments});
            shortcut.InvokeMember("WorkingDirectory", BindingFlags.SetProperty, null, link, new object[] {Root});
            shortcut.InvokeMember("IconLocation", BindingFlags.SetProperty, null, link, new object[] {Inside("assets/dictately.ico")});
            shortcut.InvokeMember("Save", BindingFlags.InvokeMethod, null, link, null);
            SHChangeNotify(0x2000, 0x5, name, IntPtr.Zero);
            SHChangeNotify(0x2000, 0x5, Inside("assets/dictately.ico"), IntPtr.Zero);
            SHChangeNotify(0x2000, 0x5, Inside("Dictately.exe"), IntPtr.Zero);
            System.Runtime.InteropServices.Marshal.FinalReleaseComObject(link);
            System.Runtime.InteropServices.Marshal.FinalReleaseComObject(shell);
        }
        internal static void CloseSettings() {
            foreach (string name in new[] {"DictatelySettings","TranscribrSettings"})
            foreach (Process process in Process.GetProcessesByName(name)) {
                using (process) {
                    string path;
                    try { path = process.MainModule.FileName; } catch { continue; }
                    if (!path.StartsWith(Root + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase)) continue;
                    process.CloseMainWindow();
                    if (!process.WaitForExit(5000)) throw new InvalidOperationException("Close Dictately settings and try again");
                }
            }
        }
        internal static void ValidateFolder() {
            Root = Normalize(Root);
            if (Root.Equals(Path.GetPathRoot(Root).TrimEnd('\\'), StringComparison.OrdinalIgnoreCase))
                throw new InvalidOperationException("Choose an app folder, not the root of a drive");
            Inside("install-manifest.txt");
            if (Directory.Exists(Root) && Directory.EnumerateFileSystemEntries(Root).Any() && !Owned())
                throw new InvalidOperationException("Choose an empty folder, or an existing Dictately installer folder");
            DriveInfo drive = new DriveInfo(Path.GetPathRoot(Root));
            if (drive.AvailableFreeSpace < 8L * 1024 * 1024 * 1024)
                throw new InvalidOperationException("Dictately needs at least 8 GB of free setup space, including temporary downloads");
        }
        internal static void Install() {
#if !UNINSTALLER
            ValidateFolder();
            Directory.CreateDirectory(Root);
            if (!Owned()) File.WriteAllText(Marker, Identity + Environment.NewLine);
            List<string> files = new List<string>();
            using (Stream stream = Assembly.GetExecutingAssembly().GetManifestResourceStream("Dictately.payload.zip"))
            using (ZipArchive zip = new ZipArchive(stream, ZipArchiveMode.Read)) {
                // The native helper can stop a previous source or installer build
                // without Python or force-killing unrelated processes.
                string temporary = Path.Combine(Path.GetTempPath(), "Dictately-" + Guid.NewGuid().ToString("N"));
                Directory.CreateDirectory(temporary);
                string close = Path.Combine(temporary, "Dictately.exe");
                zip.GetEntry("Dictately.exe").ExtractToFile(close);
                try {
                    Progress(0, "Closing the previous Dictately session…");
                    if (Run(close, "--quit-running", false) != 0)
                        throw new InvalidOperationException("Quit Dictately, then try setup again");
                } finally { File.Delete(close); Directory.Delete(temporary); }
                CloseSettings();
                // Keep prior owned entries during upgrades/repair so uninstall
                // can still remove files that disappeared from a newer payload.
                long total = zip.Entries.Sum(entry => entry.Length), copied = 0;
                foreach (ZipArchiveEntry entry in zip.Entries) {
                    Cancel.Token.ThrowIfCancellationRequested();
                    if (entry.FullName.EndsWith("/")) continue;
                    string relative = entry.FullName.Replace('/', Path.DirectorySeparatorChar);
                    string destination = Inside(relative);
                    Directory.CreateDirectory(Path.GetDirectoryName(destination));
                    entry.ExtractToFile(destination, true);
                    files.Add(relative);
                    // Record ownership as files are installed, so interrupted
                    // setup can be repaired or removed without guessing paths.
                    File.AppendAllText(Marker, relative + Environment.NewLine);
                    copied += entry.Length;
                    Progress((int)(60 * copied / Math.Max(1, total)), "Installing Dictately and its private runtime…");
                }
            }
            Log("Installing speech model");
            string arguments = "--install-model";
            if (!String.IsNullOrWhiteSpace(Cache)) arguments += " --model-cache " + Q(Normalize(Cache));
            Progress(-1, "Downloading and verifying the speech model (about 1.6 GB)…");
            if (Run(Inside(@"runtime\worker\DictatelyWorker.exe"), arguments, true) != 0)
                throw new InvalidOperationException("Speech model setup did not finish. Check your internet connection and try again. Verified downloads are reused. Details: " + Inside("setup.log"));
            Cancel.Token.ThrowIfCancellationRequested();
            if (BindingsChanged && Run(Inside("Dictately.exe"),"--set-hotkeys "+DictationKey+" "+LanguageKey,false)!=0)
                throw new InvalidOperationException("Could not save the keyboard shortcuts");
            Progress(95, "Creating shortcuts…");
            Shortcut(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Programs), "Dictately.lnk"), Inside("Dictately.exe"), "");
            Shortcut(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Programs), "Dictately Settings.lnk"), Inside("Dictately.exe"), "--settings");
            using (RegistryKey key = Registry.CurrentUser.CreateSubKey(UninstallKey)) {
                key.SetValue("DisplayName", "Dictately"); key.SetValue("DisplayVersion", Version);
                key.SetValue("Publisher", "AlthafTM"); key.SetValue("InstallLocation", Root);
                key.SetValue("DisplayIcon", Inside("Dictately.exe"));
                key.SetValue("UninstallString", Q(Inside("Uninstall Dictately.exe")));
                key.SetValue("QuietUninstallString", Q(Inside("Uninstall Dictately.exe")) + " --silent");
                key.SetValue("URLInfoAbout", "https://github.com/AlthafTM/dictately");
                key.SetValue("NoModify", 1); key.SetValue("NoRepair", 1);
            }
            using (RegistryKey key = Registry.CurrentUser.CreateSubKey(RunKey)) {
                if (Startup) key.SetValue("Dictately", Q(Inside("Dictately.exe")));
                else key.DeleteValue("Dictately", false);
                if (String.Equals(key.GetValue("Transcribr") as string,Q(Inside("Transcribr.exe")),StringComparison.OrdinalIgnoreCase)) key.DeleteValue("Transcribr",false);
            }
            using (RegistryKey key=Registry.CurrentUser.OpenSubKey(LegacyUninstallKey)) {
                if (key!=null && String.Equals(key.GetValue("InstallLocation") as string,Root,StringComparison.OrdinalIgnoreCase)) Registry.CurrentUser.DeleteSubKeyTree(LegacyUninstallKey,false);
            }
            MigrateShortcuts();
            Log("Installation complete"); Progress(100, "Ready: "+KeyLabel(DictationKey)+" to dictate · "+KeyLabel(LanguageKey)+" to choose a language");
            if (Launch) Process.Start(new ProcessStartInfo(Inside("Dictately.exe")) { WorkingDirectory = Root, UseShellExecute = false, CreateNoWindow = true });
#endif
        }
        internal static void RemoveTree(string relative) {
            string directory = Inside(relative);
            if (!Directory.Exists(directory)) return;
            if ((File.GetAttributes(directory) & FileAttributes.ReparsePoint) != 0)
                throw new InvalidOperationException("Downloaded model directories cannot be links");
            foreach (string item in Directory.EnumerateFileSystemEntries(directory)) {
                if ((File.GetAttributes(item) & FileAttributes.ReparsePoint) != 0)
                    throw new InvalidOperationException("Remove linked files from the app folder before uninstalling");
                string child = item.Substring(Root.Length + 1);
                if (Directory.Exists(item)) RemoveTree(child); else File.Delete(Inside(child));
            }
            Directory.Delete(directory);
        }
        internal static void MigrateShortcuts() {
            foreach (string name in new[] {"Transcribr.lnk","Transcribr Settings.lnk"}) {
                string path=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Programs),name);
                if (!File.Exists(path)) continue;
                Type type=Type.GetTypeFromProgID("WScript.Shell");object shell=Activator.CreateInstance(type);
                object link=type.InvokeMember("CreateShortcut",BindingFlags.InvokeMethod,null,shell,new object[] {path});
                string target=(string)link.GetType().InvokeMember("TargetPath",BindingFlags.GetProperty,null,link,null);
                if (String.Equals(target,Inside("Transcribr.exe"),StringComparison.OrdinalIgnoreCase)) File.Delete(path);
                Marshal.FinalReleaseComObject(link);Marshal.FinalReleaseComObject(shell);
            }
        }
        internal static void Uninstall() {
            Root = Normalize(AppDomain.CurrentDomain.BaseDirectory);
            if (!Owned()) throw new InvalidOperationException("This is not a managed Dictately installation");
            Progress(-1, "Closing Dictately…");
            if (Run(Inside("Dictately.exe"), "--quit-running", false) != 0)
                throw new InvalidOperationException("Quit Dictately, then try uninstall again");
            CloseSettings();
            Progress(-1, "Removing application files…");
            if (RemoveModels) RemoveTree("models");
            foreach (string relative in File.ReadAllLines(Marker).Skip(1).Distinct(StringComparer.OrdinalIgnoreCase)) {
                if (relative == "Uninstall Dictately.exe" || relative.Length == 0) continue;
                string file = Inside(relative);
                if (File.Exists(file)) File.Delete(file);
            }
            using (RegistryKey key = Registry.CurrentUser.CreateSubKey(RunKey)) {
                if (String.Equals(key.GetValue("Dictately") as string, Q(Inside("Dictately.exe")), StringComparison.OrdinalIgnoreCase))
                    key.DeleteValue("Dictately", false);
            }
            using (RegistryKey key = Registry.CurrentUser.OpenSubKey(UninstallKey)) {
                if (key != null && String.Equals(key.GetValue("InstallLocation") as string, Root, StringComparison.OrdinalIgnoreCase))
                    Registry.CurrentUser.DeleteSubKeyTree(UninstallKey, false);
            }
            foreach (string shortcutName in new[] {"Dictately.lnk","Dictately Settings.lnk"}) {
            string shortcut = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Programs), shortcutName);
            // Only remove a shortcut that points to this installation.
            if (File.Exists(shortcut)) {
                Type type = Type.GetTypeFromProgID("WScript.Shell"); object shell = Activator.CreateInstance(type);
                object link = type.InvokeMember("CreateShortcut", BindingFlags.InvokeMethod, null, shell, new object[] {shortcut});
                string target = (string)link.GetType().InvokeMember("TargetPath", BindingFlags.GetProperty, null, link, null);
                if (String.Equals(target, Inside("Dictately.exe"), StringComparison.OrdinalIgnoreCase)) File.Delete(shortcut);
                System.Runtime.InteropServices.Marshal.FinalReleaseComObject(link);
                System.Runtime.InteropServices.Marshal.FinalReleaseComObject(shell);
            }
            }
            string helper = Path.Combine(Path.GetTempPath(), "Dictately-remove-" + Guid.NewGuid().ToString("N") + ".exe");
            File.Copy(Application.ExecutablePath, helper);
            Process.Start(new ProcessStartInfo(helper, "--finish-remove " + Q(Root) + " --wait-pid " + Process.GetCurrentProcess().Id) { UseShellExecute = false, CreateNoWindow = true });
            Progress(100, "Removed: your local history and settings have been kept");
        }
        internal static void FinishRemove(string root, int pid) {
            Root = Normalize(root);
            try { using (Process owner = Process.GetProcessById(pid)) { if (!owner.WaitForExit(30000)) return; } } catch (ArgumentException) {}
            if (!Owned()) return;
            foreach (string name in new[] {"Uninstall Dictately.exe", "setup.log"}) {
                string path = Inside(name); if (File.Exists(path)) File.Delete(path);
            }
            File.Delete(Marker);
            // Remove empty owned directories only; preserve unrelated user files.
            Prune(Root);
        }
        internal static void Prune(string directory) {
            foreach (string child in Directory.GetDirectories(directory)) {
                if ((File.GetAttributes(child) & FileAttributes.ReparsePoint) == 0) Prune(child);
            }
            if (!Directory.EnumerateFileSystemEntries(directory).Any()) Directory.Delete(directory);
        }
    }

    internal sealed class ShortcutCapture : Form {
        [DllImport("user32.dll")] static extern short GetAsyncKeyState(int key);
        internal int Value;
        EventWaitHandle capture;
        internal ShortcutCapture() {
            Text="Assign shortcut";ClientSize=new Size(380,125);StartPosition=FormStartPosition.CenterParent;
            FormBorderStyle=FormBorderStyle.FixedDialog;MaximizeBox=false;MinimizeBox=false;KeyPreview=true;
            BackColor=Color.FromArgb(20,20,22);ForeColor=Color.WhiteSmoke;Font=new Font("Segoe UI",11);
            Controls.Add(new Label{Text="Press a key or key combination",Left=22,Top=24,Width=340,Height=28});
            Controls.Add(new Label{Text="Esc cancels · Modifiers can be combined",Left=22,Top=65,Width=340,Height=25,Font=new Font("Segoe UI",9)});
            capture=new EventWaitHandle(true,EventResetMode.ManualReset,@"Local\Dictately.ShortcutCapture");
            KeyDown+=delegate(object sender,KeyEventArgs e) {
                e.SuppressKeyPress=true;e.Handled=true;
                if (e.KeyCode==Keys.Escape) {DialogResult=DialogResult.Cancel;Close();return;}
                int key=(int)e.KeyCode;
                int modifiers=(e.Control?1:0)|(e.Shift?2:0)|(e.Alt?4:0)|((GetAsyncKeyState(91)<0||GetAsyncKeyState(92)<0)?8:0);
                int value=key|(modifiers<<8);
                if (Core.ValidKey(value)) {Value=value;DialogResult=DialogResult.OK;Close();}
            };
            FormClosed+=delegate {capture.Dispose();};
        }
    }

    internal class SetupWindow : Form {
        TextBox folder; CheckBox startup, launch, models; Button action, close, browse,dictation,language;
        Label status; ProgressBar progress; bool busy;
        internal SetupWindow() {
            SuspendLayout();
#if UNINSTALLER
            bool uninstall = true;
#else
            bool uninstall = false;
#endif
            Text = uninstall ? "Uninstall Dictately" : "Dictately Setup";
            ClientSize = new Size(540, uninstall?400:480); StartPosition = FormStartPosition.CenterScreen;
            FormBorderStyle = FormBorderStyle.FixedDialog; MaximizeBox = false;
            Font = new Font("Segoe UI", 10);
            BackColor = Color.FromArgb(20,20,22); ForeColor = Color.WhiteSmoke;
            Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath);
            Controls.Add(new Label {Text = uninstall ? "Remove Dictately" : "Install Dictately", Left=28, Top=24, Width=480, Height=40, Font=new Font("Segoe UI", 21, FontStyle.Bold)});
            Controls.Add(new Label {Text = uninstall ? "Your local settings and transcript history will be kept" : "Local voice typing in English and Indonesian\nSetup includes the runtime and downloads the speech model", Left=30, Top=79, Width=480, Height=50});
            folder = new TextBox {Text=Core.Root, Left=30, Top=143, Width=374, ReadOnly=uninstall, BackColor=Color.FromArgb(36,36,38), ForeColor=Color.WhiteSmoke}; Controls.Add(folder);
            browse = new Button {Text="Browse…", Left=416, Top=140, Width=94, Height=30, Enabled=!uninstall}; Controls.Add(browse);
            browse.Click += delegate { using (FolderBrowserDialog picker = new FolderBrowserDialog()) { picker.SelectedPath=folder.Text; if (picker.ShowDialog() == DialogResult.OK) folder.Text=picker.SelectedPath; } };
            startup = new CheckBox {Text="Start Dictately when I sign in to Windows", Left=30, Top=187, Width=480, Checked=Core.Startup, Visible=!uninstall}; Controls.Add(startup);
            launch = new CheckBox {Text="Open Dictately when setup finishes", Left=30, Top=216, Width=480, Checked=Core.Launch, Visible=!uninstall}; Controls.Add(launch);
            models = new CheckBox {Text="Remove downloaded speech models", Left=30, Top=187, Width=480, Checked=true, Visible=uninstall}; Controls.Add(models);
            if (!uninstall) {
                Controls.Add(new Label{Text="Microphone",Left=30,Top=257,Width=110,Height=25});
                Controls.Add(new Label{Text="Language picker",Left=280,Top=257,Width=130,Height=25});
            }
            dictation=new Button{Text=Core.KeyLabel(Core.DictationKey),Left=30,Top=284,Width=225,Height=34,Visible=!uninstall};Controls.Add(dictation);
            language=new Button{Text=Core.KeyLabel(Core.LanguageKey),Left=280,Top=284,Width=230,Height=34,Visible=!uninstall};Controls.Add(language);
            dictation.Click+=delegate {AssignKey(true);};language.Click+=delegate {AssignKey(false);};
            int offset=uninstall?0:80;
            status = new Label {Text=uninstall ? "" : "Initial setup needs internet and at least 8 GB of free space", Left=30, Top=252+offset, Width=480, Height=40}; Controls.Add(status);
            progress = new ProgressBar {Left=30, Top=300+offset, Width=480, Height=10}; Controls.Add(progress);
            action = new Button {Text=uninstall ? "Uninstall" : "Install", Left=300, Top=341+offset, Width=100, Height=34}; Controls.Add(action);
            close = new Button {Text="Close", Left=412, Top=341+offset, Width=98, Height=34}; Controls.Add(close);
            foreach (Button button in new[] {action,close,browse,dictation,language}) {
                button.FlatStyle=FlatStyle.Flat; button.FlatAppearance.BorderSize=0;
                button.BackColor=Color.Gainsboro; button.ForeColor=Color.Black;
            }
            close.Click += delegate { if (busy) { Core.Cancel.Cancel(); close.Enabled=false; status.Text="Cancelling setup…"; } else Close(); };
            FormClosing += delegate(object sender, FormClosingEventArgs e) { if (busy) { Core.Cancel.Cancel(); e.Cancel=true; } };
            action.Click += async delegate {
                busy=true; action.Enabled=false; browse.Enabled=false; folder.ReadOnly=true;
                startup.Enabled=false; launch.Enabled=false; models.Enabled=false; close.Text="Cancel";
                dictation.Enabled=false;language.Enabled=false;
                Core.Root=folder.Text; Core.Startup=startup.Checked; Core.Launch=launch.Checked; Core.RemoveModels=models.Checked;
                Core.Cancel = new CancellationTokenSource();
                Core.Progress = delegate(int amount, string text) { BeginInvoke((Action)delegate {
                    progress.Style=amount < 0 ? ProgressBarStyle.Marquee : ProgressBarStyle.Continuous;
                    if (amount >= 0) progress.Value=Math.Max(0,Math.Min(100,amount)); status.Text=text;
                }); };
                try { await Task.Run((Action)(uninstall ? (Action)Core.Uninstall : Core.Install)); action.Visible=false; close.Text="Finish"; }
                catch (OperationCanceledException) { status.Text="Setup cancelled: run setup again to resume"; action.Text="Retry"; action.Enabled=true; }
                catch (Exception error) { status.Text="Setup needs attention: you can retry"; MessageBox.Show(this,error.Message,Text,MessageBoxButtons.OK,MessageBoxIcon.Warning); action.Text="Retry"; action.Enabled=true; }
                finally { busy=false; close.Enabled=true; close.Text="Close"; folder.ReadOnly=uninstall; browse.Enabled=!uninstall; }
            };
            AutoScaleDimensions = new SizeF(96F,96F);
            AutoScaleMode = AutoScaleMode.Dpi;
            ResumeLayout(true);
        }
        void AssignKey(bool microphone) {
            using (ShortcutCapture picker=new ShortcutCapture()) {
                if (picker.ShowDialog(this)!=DialogResult.OK) return;
                if (picker.Value==(microphone?Core.LanguageKey:Core.DictationKey)) {
                    MessageBox.Show(this,"Choose different shortcuts for microphone and language",Text);return;
                }
                if (microphone) Core.DictationKey=picker.Value;else Core.LanguageKey=picker.Value;
                Core.BindingsChanged=true;dictation.Text=Core.KeyLabel(Core.DictationKey);language.Text=Core.KeyLabel(Core.LanguageKey);
            }
        }
    }
    internal static class Program {
        [STAThread] static int Main(string[] args) {
            Application.EnableVisualStyles(); Application.SetCompatibleTextRenderingDefault(false);
            Core.ReadBindings();
            Core.Root = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Programs", "Dictately");
#if UNINSTALLER
            Core.Root=AppDomain.CurrentDomain.BaseDirectory;
#else
            // Follow a previous managed installation and the user's startup choice.
            try {
                using (RegistryKey key=Registry.CurrentUser.OpenSubKey(Core.UninstallKey) ?? Registry.CurrentUser.OpenSubKey(Core.LegacyUninstallKey)) {
                    string previous=key == null ? null : key.GetValue("InstallLocation") as string;
                    if (!String.IsNullOrEmpty(previous)) {
                        string marker=Path.Combine(previous,"install-manifest.txt");
                        if (File.Exists(marker) && Core.IsIdentity(File.ReadLines(marker).FirstOrDefault())) Core.Root=previous;
                    }
                }
                using (RegistryKey key=Registry.CurrentUser.OpenSubKey(Core.RunKey)) {
                    Core.Startup=key != null && (key.GetValue("Dictately") is string || String.Equals(key.GetValue("Transcribr") as string,Core.Q(Path.Combine(Core.Root,"Transcribr.exe")),StringComparison.OrdinalIgnoreCase));
                }
            } catch { /* Ignore stale installer registry entries. */ }
#endif
            bool silent=false; string preview=null;
            for (int i=0;i<args.Length;i++) {
                if (args[i]=="--silent") silent=true;
                else if (args[i]=="--dir") Core.Root=args[++i];
                else if (args[i]=="--model-cache") Core.Cache=args[++i];
                else if (args[i]=="--startup") Core.Startup=true;
                else if (args[i]=="--no-startup") Core.Startup=false;
                else if (args[i]=="--no-launch") Core.Launch=false;
                else if (args[i]=="--keep-models") Core.RemoveModels=false;
                else if (args[i]=="--preview") preview=args[++i];
#if UNINSTALLER
                else if (args[i]=="--finish-remove") { string root=args[++i]; i++; int pid=Int32.Parse(args[++i]); Core.FinishRemove(root,pid); return 0; }
#endif
            }
            if (!Environment.Is64BitOperatingSystem) { MessageBox.Show("Dictately needs 64-bit Windows"); return 1; }
            if (preview != null) {
                using (SetupWindow window=new SetupWindow()) {
                    window.Show(); Application.DoEvents();
                    using (Bitmap image=new Bitmap(window.Width,window.Height)) { window.DrawToBitmap(image,new Rectangle(0,0,window.Width,window.Height)); image.Save(preview); }
                }
                return 0;
            }
            if (!silent) { Application.Run(new SetupWindow()); return 0; }
            try {
#if UNINSTALLER
                Core.Uninstall();
#else
                Core.Install();
#endif
                return 0;
            } catch (Exception error) { try { Core.Log(error.ToString()); } catch {} return 1; }
        }
    }
}
