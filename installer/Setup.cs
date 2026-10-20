using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Reflection;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Forms;
using Microsoft.Win32;

[assembly: AssemblyTitle("Transcribr Setup")]
[assembly: AssemblyProduct("Transcribr")]
[assembly: AssemblyCompany("AlthafTM")]
[assembly: AssemblyVersion("0.6.0.0")]
[assembly: AssemblyFileVersion("0.6.0.0")]

namespace TranscribrSetup {
    internal static class Core {
        internal const string Identity = "Transcribr:{5BFCCF8A-BAA9-48DF-917C-D1DFE4ABCF88}";
        internal const string Version = "0.6.0";
        internal const string UninstallKey = @"Software\Microsoft\Windows\CurrentVersion\Uninstall\Transcribr";
        internal const string RunKey = @"Software\Microsoft\Windows\CurrentVersion\Run";
        internal static CancellationTokenSource Cancel = new CancellationTokenSource();
        internal static Process Child;
        internal static Action<int, string> Progress = delegate {};
        internal static string Root;
        internal static string Cache;
        internal static bool Startup, Launch = true, RemoveModels = true;
        internal static string Q(string text) { return "\"" + text.Replace("\"", "\\\"") + "\""; }
        internal static string Normalize(string path) { return Path.GetFullPath(path).TrimEnd(Path.DirectorySeparatorChar); }
        internal static string Marker { get { return Path.Combine(Root, "install-manifest.txt"); } }
        internal static bool Owned() { return File.Exists(Marker) && File.ReadLines(Marker).FirstOrDefault() == Identity; }
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
                string log = Owned() ? Inside("setup.log") : Path.Combine(Path.GetTempPath(), "Transcribr-setup.log");
                File.AppendAllText(log, DateTime.Now.ToString("s") + " " + text + Environment.NewLine);
            }
        }
        internal static int Run(string file, string arguments, bool capture) {
            Cancel.Token.ThrowIfCancellationRequested();
            ProcessStartInfo info = new ProcessStartInfo(file, arguments) {
                UseShellExecute = false, CreateNoWindow = true, WorkingDirectory = Root,
                RedirectStandardOutput = capture, RedirectStandardError = capture
            };
            info.EnvironmentVariables["TRANSCRIBR_ROOT"] = Root;
            using (Process process = new Process()) {
                process.StartInfo = info;
                if (capture) {
                    DataReceivedEventHandler output = delegate(object sender, DataReceivedEventArgs e) {
                        if (e.Data == null) return;
                        Log(e.Data);
                        string message = "Preparing local speech recognition…";
                        if (e.Data.StartsWith("Downloaded")) message = "Downloading speech model — " + e.Data.Substring(11);
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
            shortcut.InvokeMember("IconLocation", BindingFlags.SetProperty, null, link, new object[] {Inside("assets/transcribr.ico")});
            shortcut.InvokeMember("Save", BindingFlags.InvokeMethod, null, link, null);
            System.Runtime.InteropServices.Marshal.FinalReleaseComObject(link);
            System.Runtime.InteropServices.Marshal.FinalReleaseComObject(shell);
        }
        internal static void CloseSettings() {
            foreach (Process process in Process.GetProcessesByName("TranscribrSettings")) {
                using (process) {
                    string path;
                    try { path = process.MainModule.FileName; } catch { continue; }
                    if (!path.StartsWith(Root + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase)) continue;
                    process.CloseMainWindow();
                    if (!process.WaitForExit(5000)) throw new InvalidOperationException("Close Transcribr settings and try again");
                }
            }
        }
        internal static void ValidateFolder() {
            Root = Normalize(Root);
            if (Root.Equals(Path.GetPathRoot(Root).TrimEnd('\\'), StringComparison.OrdinalIgnoreCase))
                throw new InvalidOperationException("Choose an app folder, not the root of a drive");
            Inside("install-manifest.txt");
            if (Directory.Exists(Root) && Directory.EnumerateFileSystemEntries(Root).Any() && !Owned())
                throw new InvalidOperationException("Choose an empty folder, or an existing Transcribr installer folder");
            DriveInfo drive = new DriveInfo(Path.GetPathRoot(Root));
            if (drive.AvailableFreeSpace < 8L * 1024 * 1024 * 1024)
                throw new InvalidOperationException("Transcribr needs at least 8 GB of free setup space, including temporary downloads");
        }
        internal static void Install() {
#if !UNINSTALLER
            ValidateFolder();
            Directory.CreateDirectory(Root);
            if (!Owned()) File.WriteAllText(Marker, Identity + Environment.NewLine);
            List<string> files = new List<string>();
            using (Stream stream = Assembly.GetExecutingAssembly().GetManifestResourceStream("Transcribr.payload.zip"))
            using (ZipArchive zip = new ZipArchive(stream, ZipArchiveMode.Read)) {
                // The native helper can stop a previous source or installer build
                // without Python or force-killing unrelated processes.
                string temporary = Path.Combine(Path.GetTempPath(), "Transcribr-" + Guid.NewGuid().ToString("N"));
                Directory.CreateDirectory(temporary);
                string close = Path.Combine(temporary, "Transcribr.exe");
                zip.GetEntry("Transcribr.exe").ExtractToFile(close);
                try {
                    Progress(0, "Closing the previous Transcribr session…");
                    if (Run(close, "--quit-running", false) != 0)
                        throw new InvalidOperationException("Quit Transcribr, then try setup again");
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
                    Progress((int)(60 * copied / Math.Max(1, total)), "Installing Transcribr and its private runtime…");
                }
            }
            Log("Installing speech model");
            string arguments = "--install-model";
            if (!String.IsNullOrWhiteSpace(Cache)) arguments += " --model-cache " + Q(Normalize(Cache));
            Progress(-1, "Downloading and verifying the speech model (about 1.6 GB)…");
            if (Run(Inside(@"runtime\worker\TranscribrWorker.exe"), arguments, true) != 0)
                throw new InvalidOperationException("Speech model setup did not finish. Check your internet connection and try again. Verified downloads are reused. Details: " + Inside("setup.log"));
            Cancel.Token.ThrowIfCancellationRequested();
            Progress(95, "Creating shortcuts…");
            Shortcut(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Programs), "Transcribr.lnk"), Inside("Transcribr.exe"), "");
            using (RegistryKey key = Registry.CurrentUser.CreateSubKey(UninstallKey)) {
                key.SetValue("DisplayName", "Transcribr"); key.SetValue("DisplayVersion", Version);
                key.SetValue("Publisher", "AlthafTM"); key.SetValue("InstallLocation", Root);
                key.SetValue("DisplayIcon", Inside("Transcribr.exe"));
                key.SetValue("UninstallString", Q(Inside("Uninstall Transcribr.exe")));
                key.SetValue("QuietUninstallString", Q(Inside("Uninstall Transcribr.exe")) + " --silent");
                key.SetValue("URLInfoAbout", "https://github.com/AlthafTM/transcribr");
                key.SetValue("NoModify", 1); key.SetValue("NoRepair", 1);
            }
            using (RegistryKey key = Registry.CurrentUser.CreateSubKey(RunKey)) {
                if (Startup) key.SetValue("Transcribr", Q(Inside("Transcribr.exe")));
                else key.DeleteValue("Transcribr", false);
            }
            Log("Installation complete"); Progress(100, "Ready — choose a language in the tray, click a text field, and press F12");
            if (Launch) Process.Start(new ProcessStartInfo(Inside("Transcribr.exe")) { WorkingDirectory = Root, UseShellExecute = false, CreateNoWindow = true });
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
        internal static void Uninstall() {
            Root = Normalize(AppDomain.CurrentDomain.BaseDirectory);
            if (!Owned()) throw new InvalidOperationException("This is not a managed Transcribr installation");
            Progress(-1, "Closing Transcribr…");
            if (Run(Inside("Transcribr.exe"), "--quit-running", false) != 0)
                throw new InvalidOperationException("Quit Transcribr, then try uninstall again");
            CloseSettings();
            Progress(-1, "Removing application files…");
            if (RemoveModels) RemoveTree("models");
            foreach (string relative in File.ReadAllLines(Marker).Skip(1).Distinct(StringComparer.OrdinalIgnoreCase)) {
                if (relative == "Uninstall Transcribr.exe" || relative.Length == 0) continue;
                string file = Inside(relative);
                if (File.Exists(file)) File.Delete(file);
            }
            using (RegistryKey key = Registry.CurrentUser.CreateSubKey(RunKey)) {
                if (String.Equals(key.GetValue("Transcribr") as string, Q(Inside("Transcribr.exe")), StringComparison.OrdinalIgnoreCase))
                    key.DeleteValue("Transcribr", false);
            }
            using (RegistryKey key = Registry.CurrentUser.OpenSubKey(UninstallKey)) {
                if (key != null && String.Equals(key.GetValue("InstallLocation") as string, Root, StringComparison.OrdinalIgnoreCase))
                    Registry.CurrentUser.DeleteSubKeyTree(UninstallKey, false);
            }
            string shortcut = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Programs), "Transcribr.lnk");
            // Only remove a shortcut that points to this installation.
            if (File.Exists(shortcut)) {
                Type type = Type.GetTypeFromProgID("WScript.Shell"); object shell = Activator.CreateInstance(type);
                object link = type.InvokeMember("CreateShortcut", BindingFlags.InvokeMethod, null, shell, new object[] {shortcut});
                string target = (string)link.GetType().InvokeMember("TargetPath", BindingFlags.GetProperty, null, link, null);
                if (String.Equals(target, Inside("Transcribr.exe"), StringComparison.OrdinalIgnoreCase)) File.Delete(shortcut);
                System.Runtime.InteropServices.Marshal.FinalReleaseComObject(link);
                System.Runtime.InteropServices.Marshal.FinalReleaseComObject(shell);
            }
            string helper = Path.Combine(Path.GetTempPath(), "Transcribr-remove-" + Guid.NewGuid().ToString("N") + ".exe");
            File.Copy(Application.ExecutablePath, helper);
            Process.Start(new ProcessStartInfo(helper, "--finish-remove " + Q(Root) + " --wait-pid " + Process.GetCurrentProcess().Id) { UseShellExecute = false, CreateNoWindow = true });
            Progress(100, "Removed — your local history and settings have been kept");
        }
        internal static void FinishRemove(string root, int pid) {
            Root = Normalize(root);
            try { using (Process owner = Process.GetProcessById(pid)) { if (!owner.WaitForExit(30000)) return; } } catch (ArgumentException) {}
            if (!Owned()) return;
            foreach (string name in new[] {"Uninstall Transcribr.exe", "setup.log"}) {
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

    internal class SetupWindow : Form {
        TextBox folder; CheckBox startup, launch, models; Button action, close, browse;
        Label status; ProgressBar progress; bool busy;
        internal SetupWindow() {
            SuspendLayout();
#if UNINSTALLER
            bool uninstall = true;
#else
            bool uninstall = false;
#endif
            Text = uninstall ? "Uninstall Transcribr" : "Transcribr Setup";
            ClientSize = new Size(540, 400); StartPosition = FormStartPosition.CenterScreen;
            FormBorderStyle = FormBorderStyle.FixedDialog; MaximizeBox = false;
            Font = new Font("Segoe UI", 10);
            BackColor = Color.FromArgb(20,20,22); ForeColor = Color.WhiteSmoke;
            Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath);
            Controls.Add(new Label {Text = uninstall ? "Remove Transcribr" : "Install Transcribr", Left=28, Top=24, Width=480, Height=40, Font=new Font("Segoe UI", 21, FontStyle.Bold)});
            Controls.Add(new Label {Text = uninstall ? "Your local settings and transcript history will be kept" : "Local voice typing in English and Indonesian\nSetup includes the runtime and downloads the speech model", Left=30, Top=79, Width=480, Height=50});
            folder = new TextBox {Text=Core.Root, Left=30, Top=143, Width=374, ReadOnly=uninstall, BackColor=Color.FromArgb(36,36,38), ForeColor=Color.WhiteSmoke}; Controls.Add(folder);
            browse = new Button {Text="Browse…", Left=416, Top=140, Width=94, Height=30, Enabled=!uninstall}; Controls.Add(browse);
            browse.Click += delegate { using (FolderBrowserDialog picker = new FolderBrowserDialog()) { picker.SelectedPath=folder.Text; if (picker.ShowDialog() == DialogResult.OK) folder.Text=picker.SelectedPath; } };
            startup = new CheckBox {Text="Start Transcribr when I sign in to Windows", Left=30, Top=187, Width=480, Checked=Core.Startup, Visible=!uninstall}; Controls.Add(startup);
            launch = new CheckBox {Text="Open Transcribr when setup finishes", Left=30, Top=216, Width=480, Checked=Core.Launch, Visible=!uninstall}; Controls.Add(launch);
            models = new CheckBox {Text="Remove downloaded speech models", Left=30, Top=187, Width=480, Checked=true, Visible=uninstall}; Controls.Add(models);
            status = new Label {Text=uninstall ? "" : "Initial setup needs internet and at least 8 GB of free space", Left=30, Top=252, Width=480, Height=40}; Controls.Add(status);
            progress = new ProgressBar {Left=30, Top=300, Width=480, Height=10}; Controls.Add(progress);
            action = new Button {Text=uninstall ? "Uninstall" : "Install", Left=300, Top=341, Width=100, Height=34}; Controls.Add(action);
            close = new Button {Text="Close", Left=412, Top=341, Width=98, Height=34}; Controls.Add(close);
            foreach (Button button in new[] {action,close,browse}) {
                button.FlatStyle=FlatStyle.Flat; button.FlatAppearance.BorderSize=0;
                button.BackColor=Color.Gainsboro; button.ForeColor=Color.Black;
            }
            close.Click += delegate { if (busy) { Core.Cancel.Cancel(); close.Enabled=false; status.Text="Cancelling setup…"; } else Close(); };
            FormClosing += delegate(object sender, FormClosingEventArgs e) { if (busy) { Core.Cancel.Cancel(); e.Cancel=true; } };
            action.Click += async delegate {
                busy=true; action.Enabled=false; browse.Enabled=false; folder.ReadOnly=true;
                startup.Enabled=false; launch.Enabled=false; models.Enabled=false; close.Text="Cancel";
                Core.Root=folder.Text; Core.Startup=startup.Checked; Core.Launch=launch.Checked; Core.RemoveModels=models.Checked;
                Core.Cancel = new CancellationTokenSource();
                Core.Progress = delegate(int amount, string text) { BeginInvoke((Action)delegate {
                    progress.Style=amount < 0 ? ProgressBarStyle.Marquee : ProgressBarStyle.Continuous;
                    if (amount >= 0) progress.Value=Math.Max(0,Math.Min(100,amount)); status.Text=text;
                }); };
                try { await Task.Run((Action)(uninstall ? (Action)Core.Uninstall : Core.Install)); action.Visible=false; close.Text="Finish"; }
                catch (OperationCanceledException) { status.Text="Setup cancelled — run setup again to resume"; action.Text="Retry"; action.Enabled=true; }
                catch (Exception error) { status.Text="Setup needs attention — you can retry"; MessageBox.Show(this,error.Message,Text,MessageBoxButtons.OK,MessageBoxIcon.Warning); action.Text="Retry"; action.Enabled=true; }
                finally { busy=false; close.Enabled=true; close.Text="Close"; folder.ReadOnly=uninstall; browse.Enabled=!uninstall; }
            };
            AutoScaleDimensions = new SizeF(96F,96F);
            AutoScaleMode = AutoScaleMode.Dpi;
            ResumeLayout(true);
        }
    }
    internal static class Program {
        [STAThread] static int Main(string[] args) {
            Application.EnableVisualStyles(); Application.SetCompatibleTextRenderingDefault(false);
            Core.Root = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Programs", "Transcribr");
#if UNINSTALLER
            Core.Root=AppDomain.CurrentDomain.BaseDirectory;
#else
            // Follow a previous managed installation and the user's startup choice.
            try {
                using (RegistryKey key=Registry.CurrentUser.OpenSubKey(Core.UninstallKey)) {
                    string previous=key == null ? null : key.GetValue("InstallLocation") as string;
                    if (!String.IsNullOrEmpty(previous)) {
                        string marker=Path.Combine(previous,"install-manifest.txt");
                        if (File.Exists(marker) && File.ReadLines(marker).FirstOrDefault()==Core.Identity) Core.Root=previous;
                    }
                }
                using (RegistryKey key=Registry.CurrentUser.OpenSubKey(Core.RunKey)) {
                    Core.Startup=key != null && key.GetValue("Transcribr") is string;
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
            if (!Environment.Is64BitOperatingSystem) { MessageBox.Show("Transcribr needs 64-bit Windows"); return 1; }
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
