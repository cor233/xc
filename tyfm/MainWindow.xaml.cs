using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.ComponentModel;
using System.IO;
using System.Linq;
using System.Net.Http;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Threading;

namespace tyfm
{
    public partial class MainWindow : Window
    {
        public class ResultItem { public int Idx { get; set; } public string Title { get; set; } = ""; public string Narrator { get; set; } = ""; public string Id { get; set; } = ""; }
        public class ChapterItem : INotifyPropertyChanged
        {
            private string _status = "待下载";
            public int Index { get; set; }
            public string Title { get; set; } = "";
            public string Status { get => _status; set { if (_status != value) { _status = value; PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(nameof(Status))); } } }
            public string Error { get; set; } = "";
            public event PropertyChangedEventHandler? PropertyChanged;
        }
        public class RecordInfo { public string Status { get; set; } = ""; public string Err { get; set; } = ""; }

        private static readonly HttpClient httpClient = new HttpClient(new HttpClientHandler { MaxConnectionsPerServer = 50 }) { Timeout = TimeSpan.FromSeconds(30) };
        private CancellationTokenSource? cts;
        private readonly object jsonLock = new object();
        private string jsonPath = "";
        private int total, done, fail;
        private Dictionary<string, RecordInfo> recordCache = new();
        private DispatcherTimer uiTimer;

        public MainWindow()
        {
            InitializeComponent();
            httpClient.DefaultRequestHeaders.Add("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36");
            httpClient.DefaultRequestHeaders.Add("Accept", "application/json, text/plain, */*");

            SearchBtn.Click += async (s, e) => await SearchAsync();
            DownloadBtn.Click += async (s, e) => await DownloadAsync();
            StopBtn.Click += (s, e) => { try { cts?.Cancel(); } catch { } LeftStatus.Text = "正在停止..."; };
            BrowseBtn.Click += (s, e) => { var d = new Microsoft.Win32.OpenFolderDialog(); if (d.ShowDialog() == true) FolderBox.Text = d.FolderName; };
            RetryBtn.Click += (s, e) => { if (ChapterGrid.ItemsSource is ObservableCollection<ChapterItem> list) { var failed = list.Where(x => x.Status == "失败").ToList(); if (failed.Count == 0) MessageBox.Show("没有失败的章节"); else MessageBox.Show($"找到 {failed.Count} 个失败章节。请重新点击下载按钮进行重试。"); } };

            uiTimer = new DispatcherTimer { Interval = TimeSpan.FromMilliseconds(300) };
            uiTimer.Tick += (s, e) => UpdateStats();
        }

        private async Task SearchAsync()
        {
            string keyword = SearchBox.Text.Trim();
            if (string.IsNullOrEmpty(keyword)) return;
            SearchBtn.IsEnabled = false;
            LeftStatus.Text = "搜索中...";
            ResultGrid.ItemsSource = null;

            try
            {
                var req = new HttpRequestMessage(HttpMethod.Post, "https://api-preview.toulaopao.cc/api/h5/listening/search");
                req.Headers.Add("Referer", "https://tingyou.fm/");
                req.Headers.Add("Origin", "https://tingyou.fm");
                req.Content = new StringContent(JsonSerializer.Serialize(new { keyword, page = 1 }), Encoding.UTF8, "application/json");
                
                var res = await httpClient.SendAsync(req);
                string rawJson = await res.Content.ReadAsStringAsync();
                
                if (string.IsNullOrWhiteSpace(rawJson) || !rawJson.TrimStart().StartsWith("{"))
                {
                    string preview = rawJson.Length > 200 ? rawJson.Substring(0, 200) : rawJson;
                    MessageBox.Show($"接口返回了非JSON数据，可能被服务器拦截或限流。\n\n返回内容预览：\n{preview}", "接口异常", MessageBoxButton.OK, MessageBoxImage.Warning);
                    LeftStatus.Text = "搜索失败: 服务器返回异常";
                    return;
                }

                using var doc = JsonDocument.Parse(rawJson);
                if (!doc.RootElement.TryGetProperty("data", out var data) || !data.TryGetProperty("list", out var listElement))
                {
                    throw new Exception("JSON结构发生变化，找不到data.list节点");
                }

                var items = listElement;
                var list = new List<ResultItem>();
                int idx = 1;
                foreach (var item in items.EnumerateArray())
                {
                    list.Add(new ResultItem { Idx = idx++, Title = item.GetProperty("title").GetString() ?? "", Narrator = item.TryGetProperty("teller", out var t) ? t.GetString() : "", Id = item.GetProperty("id").ToString() });
                }
                ResultGrid.ItemsSource = list;
                LeftStatus.Text = $"找到 {list.Count} 个专辑";
            }
            catch (Exception ex) 
            { 
                LeftStatus.Text = $"搜索异常: {ex.Message}"; 
            }
            finally { SearchBtn.IsEnabled = true; }
        }

        private async Task DownloadAsync()
        {
            if (ResultGrid.SelectedItem is not ResultItem selected) { MessageBox.Show("请先选择专辑"); return; }
            if (string.IsNullOrWhiteSpace(FolderBox.Text)) { MessageBox.Show("请先选择或输入保存目录！"); return; }
            if (!int.TryParse(ConcurrencyBox.Text, out int concurrency) || concurrency < 1) { MessageBox.Show("请输入有效的并发数！"); return; }

            int startIdx = int.TryParse(StartBox.Text, out int s1) ? s1 : 1;
            int endIdx = int.TryParse(EndBox.Text, out int e1) ? e1 : int.MaxValue;

            string saveDir = Path.Combine(FolderBox.Text, SanitizeName(selected.Title));
            Directory.CreateDirectory(saveDir);
            jsonPath = Path.Combine(saveDir, "record.json");

            ToggleUI(false);
            cts = new CancellationTokenSource();
            recordCache.Clear();

            try
            {
                LeftStatus.Text = "获取章节列表...";
                var chapReq = new HttpRequestMessage(HttpMethod.Get, $"https://appp.fdhtbz.cn/api/h5/listening/chapters/{selected.Id}?cache_meta=1");
                chapReq.Headers.Add("Referer", "https://tingyou.fm/");
                var chapRes = await httpClient.SendAsync(chapReq);
                string chapterJson = await chapRes.Content.ReadAsStringAsync();
                
                using var doc = JsonDocument.Parse(chapterJson);
                var list = doc.RootElement.GetProperty("data").GetProperty("list");
                var chapterList = new ObservableCollection<ChapterItem>();
                int idx = 1;
                foreach (var item in list.EnumerateArray())
                {
                    if (idx >= startIdx && idx <= endIdx)
                    {
                        string cTitle = item.TryGetProperty("title", out var t) ? t.GetString() : $"第{idx}集";
                        chapterList.Add(new ChapterItem { Index = idx, Title = cTitle });
                    }
                    idx++;
                }

                total = chapterList.Count;
                done = 0;
                fail = 0;
                ChapterGrid.ItemsSource = chapterList;
                await LoadRecordAsync(chapterList);
                uiTimer.Start();

                var semaphore = new SemaphoreSlim(concurrency);
                var tasks = chapterList.Select(async chap =>
                {
                    await semaphore.WaitAsync(cts.Token);
                    try
                    {
                        cts.Token.ThrowIfCancellationRequested();
                        string fileName = Path.Combine(saveDir, $"{chap.Index:D4}_{SanitizeName(chap.Title)}.m4a");

                        if (chap.Status == "完成" || File.Exists(fileName))
                        {
                            chap.Status = "完成";
                            Interlocked.Increment(ref done);
                            return;
                        }

                        var playReq = new HttpRequestMessage(HttpMethod.Post, "https://appp.fdhtbz.cn/api/h5/listening/play");
                        playReq.Headers.Add("Referer", "https://tingyou.fm/");
                        playReq.Content = new StringContent(JsonSerializer.Serialize(new { album_id = selected.Id, chapter_idx = chap.Index }), Encoding.UTF8, "application/json");
                        var playRes = await httpClient.SendAsync(playReq, cts.Token);
                        var playDoc = JsonDocument.Parse(await playRes.Content.ReadAsStringAsync());
                        string audioUrl = playDoc.RootElement.GetProperty("play_url").GetString()!;
                        
                        byte[] audioData = await httpClient.GetByteArrayAsync(audioUrl, cts.Token);
                        await File.WriteAllBytesAsync(fileName, audioData, cts.Token);

                        chap.Status = "完成";
                        Interlocked.Increment(ref done);
                        UpdateRecord(chap.Index, "完成", "");
                        Application.Current.Dispatcher.Invoke(() => LeftStatus.Text = $"下载中: {chap.Title} ({done}/{total})");
                    }
                    catch (OperationCanceledException) { chap.Status = "取消"; }
                    catch (Exception ex)
                    {
                        chap.Status = "失败";
                        chap.Error = ex.Message;
                        Interlocked.Increment(ref fail);
                        UpdateRecord(chap.Index, "失败", ex.Message);
                    }
                    finally { semaphore.Release(); }
                });

                await Task.WhenAll(tasks);
                LeftStatus.Text = "下载完成";
            }
            catch (Exception ex) { LeftStatus.Text = $"异常: {ex.Message}"; }
            finally
            {
                uiTimer.Stop();
                UpdateStats();
                SaveRecord();
                ToggleUI(true);
                cts.Dispose();
            }
        }

        private void UpdateStats()
        {
            TotalCount.Text = total.ToString();
            DoneCount.Text = done.ToString();
            FailCount.Text = fail.ToString();
        }

        private async Task LoadRecordAsync(ObservableCollection<ChapterItem> list)
        {
            if (!File.Exists(jsonPath)) return;
            try
            {
                string json = await File.ReadAllTextAsync(jsonPath);
                var dict = JsonSerializer.Deserialize<Dictionary<string, RecordInfo>>(json);
                if (dict == null) return;
                recordCache = dict;
                foreach (var kvp in dict)
                {
                    if (int.TryParse(kvp.Key, out int index))
                    {
                        var chap = list.FirstOrDefault(x => x.Index == index);
                        if (chap != null)
                        {
                            chap.Status = kvp.Value.Status == "失败" ? "失败" : "完成";
                            if (chap.Status == "完成") done++;
                            else fail++;
                        }
                    }
                }
            }
            catch { }
        }

        private void UpdateRecord(int index, string status, string err) { lock (jsonLock) { recordCache[index.ToString()] = new RecordInfo { Status = status, Err = err }; } }
        private void SaveRecord() { lock (jsonLock) { try { File.WriteAllText(jsonPath, JsonSerializer.Serialize(recordCache, new JsonSerializerOptions { WriteIndented = true })); } catch { } } }
        private void ToggleUI(bool enabled) { DownloadBtn.IsEnabled = enabled; SearchBtn.IsEnabled = enabled; StopBtn.IsEnabled = !enabled; RetryBtn.IsEnabled = enabled; SearchBox.IsEnabled = enabled; FolderBox.IsEnabled = enabled; ConcurrencyBox.IsEnabled = enabled; }
        private string SanitizeName(string name) { if (string.IsNullOrWhiteSpace(name)) return "untitled"; foreach (char c in Path.GetInvalidFileNameChars()) name = name.Replace(c, '_'); return name.Trim(); }
    }
}
