# 部署到 Render（免費）

完成後會得到一個固定網址，例如 `https://weather-system-xxxx.onrender.com`，手機、電腦都能打開。

## 1. 把程式放上 GitHub

1. 到 https://github.com/signup 註冊（已有帳號可略過）
2. 右上角 **+ → New repository**
   - Repository name：`weather-system`
   - 選 **Public**（Render 免費方案連 Private 也可以，但需授權）
   - 不要勾選「Add a README」等任何初始化選項
3. 建好後，複製頁面上的網址（像 `https://github.com/你的帳號/weather-system.git`）
4. 在這個資料夾執行（第一次推送時會跳出 GitHub 登入視窗）：

```bash
git remote add origin https://github.com/你的帳號/weather-system.git
git push -u origin main
```

## 2. 在 Render 建立網站

1. 到 https://render.com 用 **GitHub 帳號登入**
2. **New → Blueprint**，選剛剛的 `weather-system` 版本庫
3. Render 會讀取 `render.yaml`，按 **Apply / Deploy**
4. 等 5～10 分鐘建置完成，畫面上方就會出現網址

之後只要 `git push`，Render 會自動重新部署。

## 免費方案的限制

- **閒置 15 分鐘會休眠**：下一次打開要等約 1 分鐘喚醒
- **重啟後快取清空**：每個地點第一次查詢要重新訓練（約 30～90 秒）
- **記憶體 512 MB**：系統已設定同時只訓練一個地點
- **API 額度是所有訪客共用**：Open-Meteo 免費版每天 10,000 次，且限非商業用途；
  訪客多時可能暫時用完，頁面會顯示中文說明並盡量使用快取資料
- 中央氣象署警報：如有金鑰，可在 Render 的 Environment 設定 `CWA_API_KEY`
