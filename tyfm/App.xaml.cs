using System;
using System.IO;
using System.Windows;
using System.Windows.Threading;

namespace Tyfm
{
    public partial class App : Application
    {
        public static readonly string LogPath = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "startup.log");

        public static void Log(string msg)
        {
            try { File.AppendAllText(LogPath, $"[{DateTime.Now:yyyy-MM-dd HH:mm:ss}] {msg}\r\n"); } catch { }
        }

        protected override void OnStartup(StartupEventArgs e)
        {
            base.OnStartup(e);
            Log("App OnStartup Triggered");
            
            DispatcherUnhandledException += App_DispatcherUnhandledException;
            AppDomain.CurrentDomain.UnhandledException += CurrentDomain_UnhandledException;
            TaskScheduler.UnobservedTaskException += TaskScheduler_UnobservedTaskException;

            try
            {
                Log("Instantiating MainWindow...");
                var mainWindow = new MainWindow();
                Log("MainWindow Instantiated. Showing...");
                mainWindow.Show();
                Log("MainWindow Shown");
            }
            catch (Exception ex)
            {
                Log($"MainWindow Startup Exception: {ex}");
                MessageBox.Show($"Startup Failed: {ex.Message}\nCheck startup.log", "Error", MessageBoxButton.OK, MessageBoxImage.Error);
                Shutdown();
            }
        }

        private void App_DispatcherUnhandledException(object sender, DispatcherUnhandledExceptionEventArgs e)
        {
            Log($"UI Thread Unhandled Exception: {e.Exception}");
            e.Handled = true;
            MessageBox.Show($"Error: {e.Exception.Message}\nCheck startup.log");
        }

        private void CurrentDomain_UnhandledException(object sender, UnhandledExceptionEventArgs e)
        {
            Log($"App Domain Unhandled Exception: {e.ExceptionObject}");
        }

        private void TaskScheduler_UnobservedTaskException(object? sender, UnobservedTaskExceptionEventArgs e)
        {
            Log($"Task Unobserved Exception: {e.Exception}");
            e.SetObserved();
        }
    }
}
