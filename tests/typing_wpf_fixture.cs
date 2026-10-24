// Owned UI Automation editor for caret and correction regression checks.
using System;
using System.Collections.Concurrent;
using System.Threading;
using System.Web.Script.Serialization;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Interop;
using System.Windows.Threading;
class TypingFixture {
    [STAThread] static void Main() {
        Console.OutputEncoding=new System.Text.UTF8Encoding(false);
        var app = new Application();
        var panel = new StackPanel();
        var first = new Window { Title="Dictately WPF caret QA", Width=600, Height=260, Left=100, Top=100, Content=panel };
        var other = new Window { Title="Dictately WPF other QA", Width=600, Height=180, Left=100, Top=400 };
        var fields = new TextBox[3];
        for (int i=0;i<3;i++) {
            fields[i]=new TextBox { Text=i==2?"Other: ":"Existing: ", Height=55, Margin=new Thickness(10), AcceptsReturn=true };
            if(i<2) panel.Children.Add(fields[i]); else other.Content=fields[i];
        }
        var queue=new ConcurrentQueue<string>();
        var json=new JavaScriptSerializer();
        Action<object> reply=value=>{Console.WriteLine(json.Serialize(value));Console.Out.Flush();};
        first.Show();other.Show();
        reply(new {window=new WindowInteropHelper(first).Handle.ToInt64(),other=new WindowInteropHelper(other).Handle.ToInt64()});
        var reader=new Thread(()=>{string line;while((line=Console.ReadLine())!=null) queue.Enqueue(line);queue.Enqueue("quit");});
        reader.IsBackground=true;reader.Start();
        var timer=new DispatcherTimer {Interval=TimeSpan.FromMilliseconds(5)};
        timer.Tick+=(s,e)=>{
            string cmd;
            while(queue.TryDequeue(out cmd)) {
                if(cmd=="quit") {app.Shutdown();return;}
                if(cmd.StartsWith("focus ")) {
                    int i=int.Parse(cmd.Split(' ')[1]);var window=i<2?first:other;
                    window.Activate();fields[i].Focus();fields[i].Select(fields[i].Text.Length,0);
                    reply(new {focused=window.IsActive});
                } else if(cmd.StartsWith("select ")) {
                    var args=cmd.Split(' ');int a=int.Parse(args[1]),b=int.Parse(args[2]);fields[0].Select(a,b-a);reply(new {});
                } else if(cmd=="selection") reply(new []{fields[0].SelectionStart,fields[0].SelectionStart+fields[0].SelectionLength});
                else if(cmd=="read") reply(new []{fields[0].Text,fields[1].Text,fields[2].Text});
                else if(cmd=="home") {fields[0].Select(0,0);reply(new {});}
            }
        };
        timer.Start();app.Run();
    }
}
